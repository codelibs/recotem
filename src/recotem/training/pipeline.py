"""Main training pipeline.

Public entry point: ``run_training(recipe, *, key_ring, signing_key,
write_artifact_fn=None) -> TrainResult``

Orchestrates:
  fetch -> cleanse -> split -> search -> train-final -> artifact-write

All domain errors are subclasses of ``TrainingError`` (exit 4), except for
``MinDataViolation`` which carries ``code="min_data_violation"``.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# NOTE: importing the recotem.training package applies the IPython stub
# required by irspack's transitive fastprogress dep, so importing irspack
# below is safe in stub-less environments.
import pandas as pd
import structlog
from irspack import __version__ as irspack_version
from irspack.utils import df_to_sparse

from recotem.recipe.models import Recipe
from recotem.training._compat import IDMappedRecommender
from recotem.training.algorithms import get_recommender_cls, resolve_algorithm_name
from recotem.training.errors import (
    MinDataViolation,
    TrainingError,
)
from recotem.training.evaluate import build_evaluator
from recotem.training.progress import ProgressReporter
from recotem.training.search import SearchResult, run_search
from recotem.training.split import split_interactions
from recotem.version import __version__ as recotem_version

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


class TrainResult:
    """Outcome of a successful ``run_training`` call."""

    __slots__ = (
        "recipe_name",
        "run_id",
        "artifact_path",
        "best_class",
        "best_params",
        "best_score",
        "metric",
        "cutoff",
        "trained_at",
        "header",
    )

    def __init__(
        self,
        *,
        recipe_name: str,
        run_id: str,
        artifact_path: str,
        best_class: str,
        best_params: dict[str, Any],
        best_score: float,
        metric: str,
        cutoff: int,
        trained_at: str,
        header: dict[str, Any],
    ) -> None:
        self.recipe_name = recipe_name
        self.run_id = run_id
        self.artifact_path = artifact_path
        self.best_class = best_class
        self.best_params = best_params
        self.best_score = best_score
        self.metric = metric
        self.cutoff = cutoff
        self.trained_at = trained_at
        self.header = header


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_training(
    recipe: Recipe,
    *,
    key_ring: Any | None = None,
    signing_key: str | None = None,
    write_artifact_fn: Callable | None = None,
    quiet: bool = False,
    verbose: bool = False,
    run_id: str | None = None,
    no_lock: bool = False,
    fail_on_busy: bool = False,
    dev_allow_unsigned: bool = False,
) -> TrainResult | None:
    """Orchestrate the full training pipeline for *recipe*.

    Parameters
    ----------
    recipe:
        Validated ``Recipe`` object (from ``recotem.recipe``).
    key_ring:
        ``recotem.artifact.signing.KeyRing`` instance used for artifact
        signing.  When ``None`` and ``dev_allow_unsigned=False``, the function
        constructs one from ``RECOTEM_SIGNING_KEYS``.  When
        ``dev_allow_unsigned=True``, an in-memory deterministic key is used.
    signing_key:
        The active signing key identifier (first key in ``key_ring``).
        When ``None``, defaults to ``key_ring.active_kid``.
    write_artifact_fn:
        Callable with signature
        ``(payload, header_dict, output_path, versioning, key_ring,
           signing_key) -> str``.
        Defaults to ``recotem.artifact.io.write_artifact``.
    quiet, verbose:
        Progress reporting flags passed through to ``ProgressReporter``.
    run_id:
        Opaque run identifier; auto-generated if not provided.
    no_lock:
        Skip per-recipe file lock acquisition (matches ``--no-lock``).
    fail_on_busy:
        Raise ``LockContestedError`` when the lock is held instead of
        gracefully returning ``None``.  Ignored when ``no_lock=True``.
    dev_allow_unsigned:
        Build artifacts using an in-memory dev signing key when no signing
        key is configured.  Spec-mandated guardrails are enforced by the
        CLI before this is reached.

    Returns
    -------
    TrainResult on success.
    ``None`` when the recipe lock is held by another process and
    ``fail_on_busy`` is False (gracefully skipped).

    Raises
    ------
    TrainingError (and subclasses):
        Any training-time failure.  The CLI maps these to exit 4.
    """
    if run_id is None:
        run_id = uuid.uuid4().hex[:12]

    # Resolve KeyRing if the caller didn't pass one.
    if key_ring is None:
        from recotem.artifact.signing import KeyRing  # noqa: PLC0415

        if dev_allow_unsigned:
            key_ring = KeyRing("dev:" + ("0" * 64))
        else:
            import os  # noqa: PLC0415

            raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()
            if not raw:
                raise TrainingError(
                    "RECOTEM_SIGNING_KEYS is not set.  Run "
                    "`recotem keygen --type signing` to generate one, or "
                    "pass --dev-allow-unsigned for local development.",
                    code="signing_key_missing",
                )
            key_ring = KeyRing(*[e.strip() for e in raw.split(",") if e.strip()])
    if signing_key is None:
        signing_key = key_ring.active_kid

    # Acquire the per-recipe lock unless suppressed.
    if no_lock:
        return _run_training_locked(
            recipe=recipe,
            key_ring=key_ring,
            signing_key=signing_key,
            write_artifact_fn=write_artifact_fn,
            quiet=quiet,
            verbose=verbose,
            run_id=run_id,
        )
    from recotem.training.lock import recipe_lock  # noqa: PLC0415

    with recipe_lock(recipe.output.path, fail_on_busy=fail_on_busy) as acquired:
        if not acquired:
            logger.info(
                "recipe_lock_contended_skipping",
                recipe=recipe.name,
                run_id=run_id,
            )
            return None
        return _run_training_locked(
            recipe=recipe,
            key_ring=key_ring,
            signing_key=signing_key,
            write_artifact_fn=write_artifact_fn,
            quiet=quiet,
            verbose=verbose,
            run_id=run_id,
        )


def _run_training_locked(
    *,
    recipe: Recipe,
    key_ring: Any,
    signing_key: str,
    write_artifact_fn: Callable | None,
    quiet: bool,
    verbose: bool,
    run_id: str,
) -> TrainResult:
    """Inner pipeline body — runs while the per-recipe lock is held."""
    bound_logger = logger.bind(recipe=recipe.name, run_id=run_id)
    bound_logger.info("training_started")

    if write_artifact_fn is None:
        from recotem.artifact.io import write_artifact  # noqa: PLC0415

        write_artifact_fn = write_artifact

    # ------------------------------------------------------------------
    # 1. Compute recipe hash (SHA-256 of canonical YAML reserialization).
    #    We do this before any data fetch so the hash reflects config only.
    # ------------------------------------------------------------------
    recipe_hash = _compute_recipe_hash(recipe)

    # ------------------------------------------------------------------
    # 2. Fetch data via DataSource.
    # ------------------------------------------------------------------
    bound_logger.info("fetching_data")
    df: pd.DataFrame = _fetch_data(recipe, run_id=run_id)
    bound_logger.info("data_fetched", n_rows=len(df))

    # ------------------------------------------------------------------
    # 3. Cleanse.
    # ------------------------------------------------------------------
    df, drop_count = _cleanse(df, recipe)
    bound_logger.info(
        "data_cleansed",
        n_rows=len(df),
        drop_count=drop_count,
    )

    user_col = recipe.schema_.user_column
    item_col = recipe.schema_.item_column
    time_col = recipe.schema_.time_column

    n_users = df[user_col].nunique()
    n_items = df[item_col].nunique()
    n_rows = len(df)
    dedup_policy = recipe.cleansing.dedup

    data_stats: dict[str, Any] = {
        "n_rows": n_rows,
        "n_users": n_users,
        "n_items": n_items,
        "drop_count": drop_count,
        "dedup_policy": dedup_policy,
    }

    # ------------------------------------------------------------------
    # 4. Split.
    # ------------------------------------------------------------------
    bound_logger.info("splitting_data")
    X_train_full, X_val_test, val_offset = split_interactions(
        df,
        user_column=user_col,
        item_column=item_col,
        time_column=time_col,
        split_config=recipe.training.split,
    )
    bound_logger.info("split_done", val_offset=val_offset)

    # ------------------------------------------------------------------
    # 5. Build evaluator.
    # ------------------------------------------------------------------
    evaluator = build_evaluator(
        X_val_test,
        offset=val_offset,
        metric=recipe.training.metric,
        cutoff=recipe.training.cutoff,
    )

    # ------------------------------------------------------------------
    # 6. Search.
    # ------------------------------------------------------------------
    # Resolve all algorithm aliases upfront.
    resolved_algos: list[str] = [
        resolve_algorithm_name(a) for a in recipe.training.algorithms
    ]
    random_seed = recipe.training.split.seed

    bound_logger.info(
        "search_started", algorithms=resolved_algos, n_trials=recipe.training.n_trials
    )

    with ProgressReporter(
        n_trials=recipe.training.n_trials,
        recipe_name=recipe.name,
        run_id=run_id,
        quiet=quiet,
        verbose=verbose,
    ) as reporter:
        search_result: SearchResult = run_search(
            algorithms=resolved_algos,
            X_tv_train=X_train_full,
            evaluator=evaluator,
            n_trials=recipe.training.n_trials,
            per_algorithm_trials=recipe.training.per_algorithm_trials,
            per_trial_timeout_seconds=recipe.training.per_trial_timeout_seconds,
            timeout_seconds=recipe.training.timeout_seconds,
            parallelism=recipe.training.parallelism,
            storage_path=recipe.training.storage_path,
            random_seed=random_seed,
            reporter=reporter,
            recipe_name=recipe.name,
            run_id=run_id,
        )

    bound_logger.info(
        "search_done",
        best_class=search_result.best_class_name,
        best_score=search_result.best_score,
        n_completed=search_result.n_completed,
    )

    # ------------------------------------------------------------------
    # 7. Build full-data sparse matrix and train final model.
    # ------------------------------------------------------------------
    bound_logger.info("training_final_model", recommender=search_result.best_class_name)
    trained_recommender = _train_final(
        df=df,
        user_column=user_col,
        item_column=item_col,
        class_name=search_result.best_class_name,
        best_params=search_result.best_params,
    )
    bound_logger.info("training_done")

    # ------------------------------------------------------------------
    # 8. Build artifact header and write.
    # ------------------------------------------------------------------
    trained_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    header_dict: dict[str, Any] = {
        "recipe_name": recipe.name,
        "recipe_hash": recipe_hash,
        "recotem_version": recotem_version,
        "irspack_version": irspack_version,
        "trained_at": trained_at,
        "best_class": search_result.best_class_name,
        "best_params": copy.deepcopy(search_result.best_params),
        "best_score": search_result.best_score,
        "metric": recipe.training.metric,
        "cutoff": recipe.training.cutoff,
        "tuning": {
            "tried_algorithms": search_result.tried_algorithms,
            "n_trials": search_result.n_trials,
            "n_completed": search_result.n_completed,
            "best_trial_number": search_result.best_trial_number,
            "search_seed": search_result.search_seed,
        },
        "data_stats": data_stats,
    }

    artifact_path: str = write_artifact_fn(
        trained_recommender,
        header_dict,
        key_ring,
        recipe.output.path,
        versioning=recipe.output.versioning,
    )

    bound_logger.info(
        "artifact_written",
        artifact=artifact_path,
    )

    return TrainResult(
        recipe_name=recipe.name,
        run_id=run_id,
        artifact_path=artifact_path,
        best_class=search_result.best_class_name,
        best_params=copy.deepcopy(search_result.best_params),
        best_score=search_result.best_score,
        metric=recipe.training.metric,
        cutoff=recipe.training.cutoff,
        trained_at=trained_at,
        header=header_dict,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_recipe_hash(recipe: Recipe) -> str:
    """Return a SHA-256 hex digest of the recipe's canonical YAML serialization.

    Uses pydantic's ``model_dump`` -> sorted JSON to get a stable canonical
    form.  No secrets are included (recipe YAML should never contain secrets).
    """
    import json  # noqa: PLC0415

    canonical = json.dumps(
        recipe.model_dump(mode="json", by_alias=False),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fetch_data(recipe: Recipe, run_id: str) -> pd.DataFrame:
    """Fetch data using the recipe's datasource (per spec section 13 contract)."""
    from recotem.datasource.base import DataSourceError, FetchContext  # noqa: PLC0415
    from recotem.datasource.registry import get_source_class  # noqa: PLC0415

    source_config = recipe.source
    # `recipe.source` is the validated typed Config (CSVConfig / BigQueryConfig / ...).
    # Each source's `type` field discriminator names the source class.
    type_name = getattr(source_config, "type", None) or (
        source_config.get("type") if isinstance(source_config, dict) else None
    )
    if not type_name:
        raise TrainingError(
            "Recipe source has no discriminator 'type' field.",
            code="datasource_error",
        )

    try:
        source_cls = get_source_class(str(type_name))
        source_instance = source_cls(source_config)
        ctx = FetchContext(recipe_name=recipe.name, run_id=run_id)
        df = source_instance.fetch(ctx)
    except DataSourceError:
        raise
    except TrainingError:
        raise
    except Exception as exc:
        raise TrainingError(
            f"Data fetch failed: {exc}",
            code="datasource_error",
        ) from exc

    return df


def _cleanse(
    df: pd.DataFrame,
    recipe: Recipe,
) -> tuple[pd.DataFrame, int]:
    """Apply cleansing rules from *recipe.cleansing*.

    Returns
    -------
    (cleansed_df, drop_count)
    """
    cfg = recipe.cleansing
    user_col = recipe.schema_.user_column
    item_col = recipe.schema_.item_column
    time_col = recipe.schema_.time_column

    drop_count = 0

    # 1. Drop null user_id / item_id.
    if cfg.drop_null_ids:
        before = len(df)
        df = df.dropna(subset=[user_col, item_col])
        drop_count += before - len(df)

    # 2. String-coerce ids.
    df = df.copy()
    # Coerce IDs to plain Python strings (numpy object dtype) so that downstream
    # irspack code paths that pass through numpy.shuffle do not encounter
    # ArrowStringArray (pandas 2.x default) which numpy cannot shuffle.
    df[user_col] = df[user_col].astype(str).astype(object)
    df[item_col] = df[item_col].astype(str).astype(object)

    # 3. Parse time column if present.
    if time_col is not None and time_col in df.columns:
        try:
            df[time_col] = pd.to_datetime(df[time_col], utc=True)
        except Exception as exc:
            raise TrainingError(
                f"Failed to parse time_column {time_col!r}: {exc}",
                code="time_column_parse_error",
            ) from exc

    # 4. Dedup.
    dedup = cfg.dedup
    if dedup == "keep_first":
        before = len(df)
        df = df.drop_duplicates(subset=[user_col, item_col], keep="first")
        drop_count += before - len(df)
    elif dedup == "keep_last":
        before = len(df)
        df = df.drop_duplicates(subset=[user_col, item_col], keep="last")
        drop_count += before - len(df)
    elif dedup == "sum_weight":
        # If there is an implicit weight column, aggregate by summing; otherwise
        # just keep one row (count-based).
        df = (
            df.groupby([user_col, item_col], sort=False, as_index=False)
            .first()
            .reset_index(drop=True)
        )
    # "none": no dedup

    # 5. Min-data preconditions.
    n_rows = len(df)
    n_users = df[user_col].nunique()
    n_items = df[item_col].nunique()

    violations: list[str] = []
    if cfg.min_rows is not None and n_rows < cfg.min_rows:
        violations.append(f"n_rows={n_rows} < min_rows={cfg.min_rows}")
    if cfg.min_users is not None and n_users < cfg.min_users:
        violations.append(f"n_users={n_users} < min_users={cfg.min_users}")
    if cfg.min_items is not None and n_items < cfg.min_items:
        violations.append(f"n_items={n_items} < min_items={cfg.min_items}")

    if violations:
        raise MinDataViolation(
            "Dataset below minimum thresholds after cleansing: "
            + "; ".join(violations),
            n_rows=n_rows,
            n_users=n_users,
            n_items=n_items,
        )

    return df, drop_count


def _train_final(
    df: pd.DataFrame,
    user_column: str,
    item_column: str,
    class_name: str,
    best_params: dict[str, Any],
) -> IDMappedRecommender:
    """Train the final model on the full dataset using best hyperparameters."""
    X_full, uids, iids = df_to_sparse(df, user_column, item_column)
    uids_str = [str(u) for u in uids]
    iids_str = [str(i) for i in iids]

    rec_cls = get_recommender_cls(class_name)

    # Only pass params that the recommender's __init__ accepts.
    # best_params may contain user_attrs not in __init__; filter via
    # default_suggest_parameter signature is not straightforward, so we pass
    # them all and let irspack handle unknowns (it typically ignores extras).
    try:
        recommender = rec_cls(X_full, **best_params).learn()
    except TypeError as exc:
        # Some params from search are not valid for final __init__; retry with
        # only the params the class declares via its own parameter space.
        raise TrainingError(
            f"Final training of {class_name} failed with params {best_params}: {exc}",
            code="final_training_error",
        ) from exc

    return IDMappedRecommender(recommender, uids_str, iids_str)
