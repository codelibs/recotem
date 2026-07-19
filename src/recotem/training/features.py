"""Fetch feature tables and encode them onto a given axis.

Training is the only caller. The pure encoding lives in ``recotem._features``
so serving can reuse it without importing this package.

Feature tables are fetched through the datasource registry, exactly like the
interaction source. That is sound because ``FetchContext`` carries no
interaction semantics -- it holds only ``recipe_name``, ``run_id``, and an
``extra`` dict -- and BigQuery/SQL sources return the fetched DataFrame
unmodified.

Do NOT put ``user_column`` / ``item_column`` into ``FetchContext.extra``: that
would wake ``datasource/csv.py``'s ``_validate_required_columns``, which is
currently inert because the production call site (``pipeline.py``) passes no
``extra``, and it would reject a feature table for lacking interaction
columns.

Error-mapping decision
-----------------------
``build_encoder_state`` (``recotem._features``) raises ``FeatureEncodeError``
for a missing feature column or a dimension over ``RECOTEM_MAX_FEATURE_DIM``.
That exception is deliberately NOT a ``TrainingError`` subclass -- it is
shared with ``recotem.serving``, which must never import
``recotem.training.errors`` (training/serving isolation). Left unhandled it
would surface as an unmapped exception (exit 1) instead of the documented
training-domain exit code (4), so ``_fetch_side`` catches it here, at the
training/neutral boundary, and re-raises ``TrainingError(code=
"feature_table_error")``. This gives every feature-table failure -- a bad
``id_column``, an unresolvable ``columns`` entry, or a dimension-cap breach
-- the same exit code as every other training-domain error (``SplitError``,
``SearchError``, ...), which is what operators and the CLI exit-code table
expect.

``DataSourceError`` (unknown source type, CSV parse failure, BigQuery access
failure, ...) is deliberately NOT wrapped: it propagates unchanged, exactly
like the main interaction source's fetch in ``pipeline.py:_fetch_data``, so
it keeps mapping to exit 3.

Why id coverage is checked at ENCODE time, not fetch time
----------------------------------------------------------
A feature table whose ids do not match the interaction axis is the one
feature-aware failure that is otherwise SILENT: every entity encodes to the
bias column alone, training completes, and the signed artifact's header
advertises ``features`` for what is really plain iALS. Two independent causes
converge on that same outcome -- an id dtype/format mismatch (a single blank
cell makes pandas infer ``float64``, so ``1`` reads back as ``1.0`` while the
interaction axis carries ``"1"``), and a wrong-but-existing ``id_column``,
which sails through ``_fetch_side``'s presence check.

``_check_axis_coverage`` catches both, because it keys off the observable
they share rather than the causes they do not: zero id overlap with the axis
being encoded. It necessarily runs in ``encode_for_axis`` and not in
``_fetch_side`` -- ``load_feature_tables`` runs BEFORE the split
(``pipeline.py`` step 2.5 vs. step 4), so the interaction axis does not exist
yet at fetch time. ``encode_for_axis`` is the first point where both sides
are known.

Coercing the id column defensively at fetch time was considered and rejected
as the primary fix. By the time ``_fetch_side`` sees the frame, ``pd.read_csv``
has already inferred ``float64``, and the original text is unrecoverable: a
column reading ``1.0`` is indistinguishable from one whose ids are literally
``"1.0"``, so "reformat integral floats as ints" would silently REWRITE ids on
a catalog that legitimately uses that form -- trading a detectable failure for
a quiet corruption. It also would not catch the wrong-``id_column`` case at
all. Reading the id column as a string at the SOURCE is the real remedy, and
it is what the error message points the operator to -- but the mechanism is
source-specific (``dtype: {id: str}`` exists only on ``csv``; ``bigquery`` /
``sql`` need a ``CAST(... AS STRING)`` in the query, and ``parquet`` a schema
fix), so the message links the per-source matrix in ``docs/operations.md``
rather than naming a key that a non-``csv`` source does not have. The check is
what makes the need for it visible instead of silent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import scipy.sparse as sps
import structlog

from recotem._features import FeatureEncodeError, build_encoder_state, encode
from recotem.datasource.base import FetchContext
from recotem.datasource.registry import get_source_class
from recotem.recipe.models import FeaturesConfig, FeatureSideConfig
from recotem.training.errors import TrainingError

logger = structlog.get_logger(__name__)

# Ids sampled into the zero-overlap error message. Three per side is enough to
# make a systematic format difference ("1.0" vs "1") self-evident.
#
# This is a deliberate, bounded disclosure of ids that ARE otherwise sensitive
# -- and they do NOT stay in the operator's terminal. The message becomes the
# raised TrainingError, which pipeline.py surfaces as ``error=str(exc)`` inside
# the ``train_error`` log event, so these ids travel to whatever sink ingests
# that event (Cloud Logging / DataDog / CI logs), verbatim. ``log_redaction`` is
# keyed to credential/key SHAPES (hex64, cloud creds) and does not touch ids, so
# it does not scrub them. The disclosure is judged worth it -- three sampled ids
# are the only thing that makes a "1.0" vs "1" mismatch diagnosable, and the
# operator already has read access to both tables -- but it is a real export, so
# BOTH the count and each id's LENGTH are bounded: an unbounded id (a stray
# multi-MB cell) would otherwise inflate the event, and the count bound alone
# does not stop that.
_ID_SAMPLE_SIZE = 3
_ID_SAMPLE_MAX_CHARS = 64


@dataclass(frozen=True)
class FeatureTables:
    """Fetched feature frames plus their phase-independent encoder states."""

    item_state: dict | None = None
    item_df: pd.DataFrame | None = None
    user_state: dict | None = None
    user_df: pd.DataFrame | None = None

    @property
    def enabled(self) -> bool:
        return self.item_state is not None or self.user_state is not None


def _spec_is_live(spec: dict) -> bool:
    """True if an encoder-state column spec can emit a non-bias feature.

    A ``numerical`` spec always reserves ``width == 1`` even when it is dead
    (std floored to 0.0, emitting nothing at encode time), so its width is not
    a reliable liveness signal -- its ``std`` is. A ``categorical`` /
    ``multi_label`` spec, by contrast, prunes to ``width == 0`` when dead, so
    its width is exactly right. The whole-block-dead guard in ``_fetch_side``
    keys on this rather than on ``n_features``.
    """
    if spec["encoding"] == "numerical":
        return spec["std"] != 0.0
    return spec["width"] > 0


def _resolve_source(source_cfg: Any, *, which: str) -> tuple[type, Any]:
    """Return ``(source_cls, config)`` for a ``FeatureSideConfig.source`` value.

    ``features.<side>.source`` arrives already-typed (a ``CSVConfig`` /
    ``BigQueryConfig`` / ... instance) when the recipe went through
    ``recipe.loader.load_recipe`` -- the production path, since
    ``pipeline.py`` passes ``recipe.features`` and the loader performs the
    typed construction for every feature subtree. Direct construction of
    ``FeatureSideConfig`` -- as unit tests and library callers do -- leaves
    ``source`` as a raw dict, because ``FeatureSideConfig.source`` is typed
    ``Any``. Both shapes are handled here so this module does not depend on
    every caller going through the loader.
    """
    type_name = getattr(source_cfg, "type", None)
    if type_name is None and isinstance(source_cfg, dict):
        type_name = source_cfg.get("type")
    if not type_name:
        raise TrainingError(
            f"features.{which}.source has no discriminator 'type' field.",
            code="feature_table_error",
        )

    # Unknown type_name -> DataSourceError, propagated unchanged (exit 3).
    source_cls = get_source_class(str(type_name))

    if isinstance(source_cfg, dict):
        try:
            config = source_cls.Config.model_validate(source_cfg)
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise TrainingError(
                f"features.{which}.source failed validation: {exc}",
                code="feature_table_error",
            ) from exc
    else:
        config = source_cfg

    return source_cls, config


def _fetch_side(
    side: FeatureSideConfig,
    *,
    which: str,
    recipe_name: str,
    run_id: str,
) -> tuple[dict, pd.DataFrame]:
    """Fetch one side's feature table and build its phase-independent state.

    Raises
    ------
    DataSourceError
        Propagated unchanged from the datasource fetch (unknown source
        type, CSV parse failure, BigQuery access failure, ...) -- exit 3,
        same as the main interaction source.
    TrainingError
        For anything about the *shape* of the fetched table: a missing
        ``id_column``, or ``build_encoder_state`` rejecting a ``columns``
        entry / a dimension-cap breach -- exit 4.
    """
    source_cls, config = _resolve_source(side.source, which=which)
    ctx = FetchContext(recipe_name=recipe_name, run_id=run_id)
    df = source_cls(config).fetch(ctx)

    if side.id_column not in df.columns:
        raise TrainingError(
            f"features.{which}.id_column {side.id_column!r} is not present "
            f"in the fetched feature table; available columns: "
            f"{sorted(df.columns)}",
            code="feature_table_error",
        )

    frame = df.copy()

    # Detect null/empty id BEFORE str-coercion (mirrors metadata/loader.py)
    # so that an entity literally named the string "nan" is preserved as a
    # real id rather than mistaken for a missing one.
    null_mask = frame[side.id_column].isna() | (
        frame[side.id_column].astype(str).str.strip() == ""
    )
    null_count = int(null_mask.sum())
    if null_count:
        logger.warning(
            "feature_table_null_ids_dropped",
            side=which,
            drop_count=null_count,
        )
        frame = frame[~null_mask]

    # Coerce to plain Python str (numpy object dtype), matching pipeline.py's
    # id-coercion convention -- avoids ArrowStringArray (pandas 2.x default).
    frame[side.id_column] = frame[side.id_column].astype(str).astype(object)
    duplicate_count = int(frame[side.id_column].duplicated(keep="first").sum())
    if duplicate_count:
        logger.warning(
            "feature_table_duplicate_ids_dropped",
            side=which,
            drop_count=duplicate_count,
        )
    frame = frame.drop_duplicates(subset=[side.id_column], keep="first")
    frame = frame.set_index(side.id_column)

    try:
        state = build_encoder_state(frame, side.columns)
    except FeatureEncodeError as exc:
        raise TrainingError(
            f"features.{which}: {exc}",
            code="feature_table_error",
        ) from exc

    # Whole-block-dead guard. The block is dead when NO declared column can
    # emit a non-bias feature: every entity then encodes to bias alone --
    # byte-identical to plain iALS -- yet training would COMPLETE and sign an
    # artifact whose header advertises ``features``. That is the same silent
    # outcome ``_check_axis_coverage`` refuses at 0% id overlap, reached by a
    # third route, so it is refused here with the same posture.
    # ``build_encoder_state`` only WARNS per column (it is a neutral module
    # shared with serving and must not raise a training error); this
    # training-side check is where the whole-block refusal belongs.
    #
    # Liveness is per encoding, NOT ``n_features``: a categorical/multi_label
    # spec that pruned to width 0 contributes nothing, but a NUMERICAL spec
    # always reserves width 1 even when its std was floored to 0.0 (dead) and
    # it emits nothing at encode time. Keying on ``n_features == 1`` therefore
    # missed an all-dead-NUMERICAL block (n_features stays 2). A single dead
    # column among several live ones is NOT refused -- pruning one column via
    # ``min_frequency`` is a legitimate operator choice -- so this fires only
    # when EVERY spec is dead.
    if not any(_spec_is_live(s) for s in state["columns"]):
        raise TrainingError(
            f"features.{which}: every declared feature column encodes to "
            f"nothing, so the whole block collapses to the bias column alone "
            f"-- training would otherwise succeed and sign an artifact "
            f"advertising features for what is really plain iALS. Usual "
            f"causes: a min_frequency higher than any token's count (it prunes "
            f"the entire vocabulary), a feature column with no usable values, "
            f"or a numerical column with zero (or near-zero) variance. Lower "
            f"min_frequency, fix the source column, or drop the empty "
            f"features.{which} block.",
            code="feature_table_error",
        )

    logger.info(
        "feature_table_loaded",
        side=which,
        n_rows=int(frame.shape[0]),
        n_features=state["n_features"],
        # Column NAMES only. Feature values are user PII and must never be
        # logged.
        columns=[s["name"] for s in state["columns"]],
    )
    return state, frame


def load_feature_tables(
    features: FeaturesConfig | None,
    *,
    recipe_name: str,
    run_id: str,
) -> FeatureTables:
    """Fetch every configured feature table and build its encoder state.

    The state is built once here and is phase-independent; only the row
    order differs between the search phase and the final refit, and that is
    supplied per-call to :func:`encode_for_axis`.
    """
    if features is None:
        return FeatureTables()

    item_state = item_df = user_state = user_df = None
    if features.item is not None:
        item_state, item_df = _fetch_side(
            features.item, which="item", recipe_name=recipe_name, run_id=run_id
        )
    if features.user is not None:
        user_state, user_df = _fetch_side(
            features.user, which="user", recipe_name=recipe_name, run_id=run_id
        )
    return FeatureTables(
        item_state=item_state,
        item_df=item_df,
        user_state=user_state,
        user_df=user_df,
    )


def _id_sample(values: Iterable[Any]) -> list[str]:
    """The first few ids, str-normalized exactly as ``encode`` normalizes them.

    Bounds BOTH the count (``_ID_SAMPLE_SIZE``) and each id's length
    (``_ID_SAMPLE_MAX_CHARS``): the sample lands in an exception message that
    ships to the log stream (see ``_ID_SAMPLE_SIZE``'s comment), so a single
    multi-MB id cell would otherwise bloat the event even though only three ids
    are taken. Truncation keeps the diagnostic value -- a "1.0" vs "1" prefix
    mismatch is still visible in the first characters -- while capping bytes.
    """
    out: list[str] = []
    for v in values:
        s = str(v)
        if len(s) > _ID_SAMPLE_MAX_CHARS:
            s = f"{s[:_ID_SAMPLE_MAX_CHARS]}...(+{len(s) - _ID_SAMPLE_MAX_CHARS} chars)"
        out.append(s)
        if len(out) >= _ID_SAMPLE_SIZE:
            break
    return out


def _check_axis_coverage(
    df: pd.DataFrame,
    index_order: Sequence[str],
    *,
    which: str,
) -> None:
    """Log how much of *index_order* the feature table covers; refuse at zero.

    See the module docstring for why this lives here rather than in
    ``_fetch_side``, and why it is preferred over coercing ids at fetch time.

    Only ZERO overlap is refused. A partially-covering feature table is
    legitimate by design -- ``build_encoder_state`` builds its vocabulary from
    the whole table precisely so cold-start entities are representable, and an
    id absent from the table encodes to bias-only, degrading that one entity to
    plain iALS. There is deliberately no low-coverage WARNING threshold: an id
    dtype/format is a property of the whole column and an ``id_column`` is
    either right or wrong, so every systematic mismatch lands at exactly 0%,
    never at 5% or 20%. A nonzero-but-low coverage therefore carries no
    evidence of a bug -- it only reflects how much of the catalog the operator's
    table happens to cover -- so any threshold above zero would fire on correct
    configurations and teach operators to ignore the warning. The INFO line
    below carries matched/total for anyone who wants to alert on it themselves.
    """
    # Normalize both sides exactly as ``encode`` does, or the coverage
    # reported here would not be the coverage ``encode`` actually achieves.
    order = {str(i) for i in index_order}
    if not order:
        # An empty axis has nothing to cover: 0 matched is not a mismatch, and
        # 0/0 is not a ratio worth reporting. An interaction table with no
        # items/users is a different failure, already caught upstream by the
        # min_users / min_items preconditions.
        return

    feature_ids = {str(i) for i in df.index}
    matched = len(order & feature_ids)

    if matched == 0:
        # _fetch_side set the index from side.id_column, so the index carries
        # the configured name; fall back for a directly-constructed
        # FeatureTables (library/test callers -- see _resolve_source).
        id_column = df.index.name or f"features.{which}.id_column"
        raise TrainingError(
            f"features.{which}: none of the {len(order)} {which} ids in the "
            f"interaction data were found in the feature table's "
            f"{id_column!r} column, so every {which} would encode to the bias "
            f"column alone -- training would otherwise succeed and sign an "
            f"artifact advertising features for what is really plain iALS. "
            f"feature-table ids look like {_id_sample(df.index)}; interaction "
            f"ids look like {_id_sample(index_order)}. Usual causes: an id "
            f"dtype mismatch (one blank cell makes pandas read an integer id "
            f"column as float, turning 1 into 1.0), or a "
            f"features.{which}.id_column naming the wrong column. The remedy is "
            f"to ensure the id column is read as a string at the SOURCE; the "
            f"exact mechanism is source-specific (csv, bigquery, sql, and "
            f"parquet each differ) -- see docs/operations.md#recotem-train-"
            f"exits-4-with-feature_axis_error.",
            code="feature_axis_error",
        )

    logger.info(
        "feature_axis_coverage",
        side=which,
        matched=matched,
        total=len(order),
        # Counts only -- ids are user PII, so this healthy-path event carries
        # none, the same column-NAMES-only rule as feature_table_loaded above.
        # The bounded id sample is confined to the FATAL zero-overlap path: it
        # is a deliberate, bounded disclosure (count and per-id length both
        # capped) because it is the only thing that makes a "1.0" vs "1"
        # mismatch diagnosable. Be precise about where it goes, though -- it is
        # NOT "shown once to the operator": that message becomes the raised
        # TrainingError, which pipeline.py logs as error=str(exc) in the
        # train_error event, so the sampled ids reach the same log sink as this
        # event. That export is the reason both bounds exist; see _ID_SAMPLE_SIZE.
    )


def encode_for_axis(
    tables: FeatureTables,
    *,
    item_order: Sequence[str] | None,
    user_order: Sequence[str] | None,
) -> dict[str, sps.csr_matrix]:
    """Encode the configured sides onto the supplied row orders.

    Call once per phase, and never cache or reuse the result across phases:
    the search phase and the final refit have DIFFERENT item orderings
    (``list(set(...))`` vs ``pd.Categorical``), and the search order is not
    even stable across processes for string ids. irspack accepts a
    misordered feature matrix silently, so a cached matrix would not be an
    optimization -- it would be a silently wrong model.
    """
    out: dict[str, sps.csr_matrix] = {}
    if tables.item_state is not None:
        if item_order is None:
            raise TrainingError(
                "internal: item features are configured but no item_order "
                "was supplied to encode_for_axis",
                code="feature_axis_error",
            )
        _check_axis_coverage(tables.item_df, item_order, which="item")
        out["item_features"] = encode(
            tables.item_state, tables.item_df, index_order=item_order
        )
    if tables.user_state is not None:
        if user_order is None:
            raise TrainingError(
                "internal: user features are configured but no user_order "
                "was supplied to encode_for_axis",
                code="feature_axis_error",
            )
        _check_axis_coverage(tables.user_df, user_order, which="user")
        out["user_features"] = encode(
            tables.user_state, tables.user_df, index_order=user_order
        )
    return out
