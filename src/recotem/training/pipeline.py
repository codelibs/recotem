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
# Exit-code mapping (mirrors cli._map_exception_to_exit without importing cli)
# ---------------------------------------------------------------------------

_EXIT_SUCCESS = 0
_EXIT_UNKNOWN = 1
_EXIT_RECIPE = 2
_EXIT_DATASOURCE = 3
_EXIT_TRAINING = 4
_EXIT_ARTIFACT = 5
_EXIT_LOCK_CONTESTED = 6
_EXIT_HTTP_FETCH = 7
_EXIT_CONFIG = 8


def _map_exception_to_exit(exc: BaseException) -> int:
    """Map a known exception type to its canonical exit code.

    A training-side copy of the same mapping that lives in cli.py, so that
    pipeline.py can include ``exit_code`` in ``train_error`` events without
    importing the CLI module.
    """
    if (
        isinstance(exc, TrainingError)
        and getattr(exc, "code", "") == "signing_key_missing"
    ):
        return _EXIT_CONFIG

    try:
        from recotem.recipe.errors import RecipeError as _RecipeError  # noqa: PLC0415

        if isinstance(exc, _RecipeError):
            return _EXIT_RECIPE
    except ImportError:
        pass

    # HTTP fetch errors are checked BEFORE DataSourceError so that a
    # DataSourceError wrapping an HttpFetchError still maps to exit 7.
    # CronJob retry semantics distinguish transient HTTP/SSRF failures (7)
    # from structural data-source failures (3).
    try:
        from recotem._http_fetch import (
            HttpFetchError as _HttpFetchError,  # noqa: PLC0415
        )

        cur: BaseException | None = exc
        while cur is not None:
            if isinstance(cur, _HttpFetchError):
                return _EXIT_HTTP_FETCH
            cur = cur.__cause__
    except (ImportError, AttributeError):
        pass

    try:
        from recotem.datasource.base import (
            DataSourceError as _DataSourceError,  # noqa: PLC0415
        )

        if isinstance(exc, _DataSourceError):
            return _EXIT_DATASOURCE
    except ImportError:
        pass

    try:
        from recotem.artifact.format import (
            ArtifactError as _ArtifactError,  # noqa: PLC0415
        )

        if isinstance(exc, _ArtifactError):
            return _EXIT_ARTIFACT
    except ImportError:
        pass

    try:
        from recotem.training.lock import (
            LockContestedError as _LockContestedError,  # noqa: PLC0415
        )

        if isinstance(exc, _LockContestedError):
            return _EXIT_LOCK_CONTESTED
    except (ImportError, AttributeError):
        pass

    if isinstance(exc, TrainingError):
        return _EXIT_TRAINING

    return _EXIT_UNKNOWN


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
        "kid",
        "trials",
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
        kid: str,
        trials: int,
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
        self.kid = kid
        self.trials = trials


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
        ``(payload_obj, header_dict, key_ring, fs_path, *, versioning) -> str``.
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

    try:
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
                key_ring = KeyRing(raw)
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
    except Exception as exc:
        # Canonical end-of-train marker for failure path.  Library callers
        # of run_training() get this event regardless of whether they wrap
        # the call themselves; pairs with the train_done event emitted
        # inside _run_training_locked on success.
        # For domain errors (TrainingError subclasses), use the declared code.
        # For unexpected non-domain errors (KeyError, AttributeError, etc.),
        # emit code="internal_error" so operators can distinguish bugs from
        # expected failure modes when alerting on the code field.
        from recotem.training.errors import (
            MinDataViolation as _MinDataViolation,  # noqa: PLC0415
        )
        from recotem.training.errors import (
            TrainingError as _TrainingError,  # noqa: PLC0415
        )

        declared_code = getattr(exc, "code", None)
        if declared_code:
            error_code = declared_code
        elif isinstance(exc, _TrainingError):
            error_code = "training_error"
        else:
            error_code = "internal_error"

        exit_code = _map_exception_to_exit(exc)

        # Build extra diagnostic fields for specific error types.
        extra: dict[str, Any] = {}
        if isinstance(exc, _MinDataViolation):
            for attr in (
                "n_rows",
                "n_users",
                "n_items",
                "min_rows",
                "min_users",
                "min_items",
            ):
                val = getattr(exc, attr, None)
                if val is not None:
                    extra[attr] = val

        logger.error(
            "train_error",
            recipe=recipe.name,
            run_id=run_id,
            error=str(exc),
            code=error_code,
            exit_code=exit_code,
            trained_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **extra,
        )
        raise


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
            metric=recipe.training.metric,
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
    bound_logger.info("final_model_trained")

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
            "n_orphaned": search_result.orphaned_count,
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

    # Canonical end-of-train marker.
    # Schema: name, run_id, exit_code, artifact, best_class, best_score,
    # trials, n_orphaned, trained_at, kid.  Use the unbound logger so the
    # event keys do not duplicate bound context fields.
    logger.info(
        "train_done",
        name=recipe.name,
        run_id=run_id,
        exit_code=0,
        artifact=artifact_path,
        best_class=search_result.best_class_name,
        best_score=search_result.best_score,
        trials=search_result.n_completed,
        n_orphaned=search_result.orphaned_count,
        trained_at=trained_at,
        kid=signing_key,
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
        kid=signing_key,
        trials=search_result.n_completed,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_paths_for_hash(obj: Any) -> Any:
    """Recursively convert Path-like objects to POSIX strings for stable hashing.

    ``pathlib.Path`` (and its subclasses such as ``PurePosixPath`` and
    ``PureWindowsPath``) serialise via ``str()`` to an OS-dependent
    representation: POSIX gives ``/data/foo`` while Windows gives
    ``\\data\\foo``.  Using ``Path.as_posix()`` normalises to the forward-
    slash form on every platform so the same recipe always produces the same
    hash regardless of where ``_compute_recipe_hash`` is called.
    """
    import pathlib  # noqa: PLC0415

    if isinstance(obj, pathlib.PurePath):
        return obj.as_posix()
    if isinstance(obj, dict):
        return {k: _normalize_paths_for_hash(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_paths_for_hash(v) for v in obj]
    return obj


def _json_default_for_hash(obj: Any) -> Any:
    """Custom JSON default serialiser for ``_compute_recipe_hash``.

    Converts ``pathlib.PurePath`` to a POSIX string before falling back to
    ``str()`` for any other non-serialisable type.  This keeps the same
    safety net as the previous ``default=str`` while guaranteeing that Paths
    are never serialised with a OS-dependent separator.
    """
    import pathlib  # noqa: PLC0415

    if isinstance(obj, pathlib.PurePath):
        return obj.as_posix()
    return str(obj)


def _compute_recipe_hash(recipe: Recipe) -> str:
    """Return a SHA-256 hex digest of the recipe's canonical YAML serialization.

    Uses pydantic's ``model_dump`` -> sorted JSON to get a stable canonical
    form.  No secrets are included (recipe YAML should never contain secrets).

    Path normalisation: any ``pathlib.PurePath`` (including ``PureWindowsPath``)
    found in the dump is converted to a POSIX forward-slash string via
    ``as_posix()`` so the hash is identical on POSIX and Windows hosts given
    the same recipe content.
    """
    import json  # noqa: PLC0415

    raw = recipe.model_dump(mode="json", by_alias=False)
    normalised = _normalize_paths_for_hash(raw)
    canonical = json.dumps(
        normalised,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default_for_hash,
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
        # Unexpected exceptions from the datasource path map to DataSourceError
        # (exit 3), not TrainingError (exit 4), per the documented exit-code
        # contract in docs/operations.md.
        raise DataSourceError(f"Data fetch failed: {exc}") from exc

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
            col_dtype = df[time_col].dtype
            if pd.api.types.is_numeric_dtype(col_dtype):
                # Numeric columns require an explicit time_unit to avoid
                # silent ns-interpretation that maps Unix epoch seconds to
                # dates near 1970-01-01 00:00:00 rather than their intended
                # values.  See docs/recipe-reference.md.
                time_unit = recipe.schema_.time_unit
                if time_unit is None:
                    raise TrainingError(
                        f"time_column {time_col!r} contains numeric values but "
                        "schema.time_unit is not set.  Specify time_unit ('s', "
                        "'ms', 'us', or 'ns') to avoid silent nanosecond "
                        "interpretation of Unix timestamps.",
                        code="time_unit_required",
                    )
                df[time_col] = pd.to_datetime(df[time_col], unit=time_unit, utc=True)
            else:
                df[time_col] = pd.to_datetime(df[time_col], utc=True)
        except TrainingError:
            raise
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
            min_rows=cfg.min_rows,
            min_users=cfg.min_users,
            min_items=cfg.min_items,
        )

    return df, drop_count


def _train_final(
    df: pd.DataFrame,
    user_column: str,
    item_column: str,
    class_name: str,
    best_params: dict[str, Any],
) -> IDMappedRecommender:
    """Train the final model on the full dataset using best hyperparameters.

    ``best_params`` may carry keys outside the recommender's ``__init__``
    signature (TPESampler-injected names, ``user_attrs.learnt_config``
    overlays, etc.).  Forwarding those to ``rec_cls(X_full, **best_params)``
    raises ``TypeError`` after a successful 100% search — the artifact never
    gets written.  Filter to ``__init__``-accepted keys before constructing,
    and log any dropped names so operators can investigate plugin/version
    drift.
    """
    import inspect as _inspect

    X_full, uids, iids = df_to_sparse(df, user_column, item_column)
    uids_str = [str(u) for u in uids]
    iids_str = [str(i) for i in iids]

    rec_cls = get_recommender_cls(class_name)

    try:
        sig = _inspect.signature(rec_cls.__init__)
        accepted = {
            name
            for name, p in sig.parameters.items()
            if p.kind
            in (
                _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                _inspect.Parameter.KEYWORD_ONLY,
            )
        }
        # Any **kwargs sink means the constructor accepts everything.
        accepts_var_kw = any(
            p.kind is _inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        # ``inspect.signature`` can fail on C-extension classes; fall back to
        # forwarding every key and let the construction surface the error.
        accepted = set(best_params)
        accepts_var_kw = True

    if accepts_var_kw:
        filtered = best_params
    else:
        dropped = sorted(k for k in best_params if k not in accepted)
        filtered = {k: v for k, v in best_params.items() if k in accepted}
        if dropped:
            logger.warning(
                "final_training_dropped_params",
                class_name=class_name,
                dropped=dropped,
            )

    try:
        recommender = rec_cls(X_full, **filtered).learn()
    except TypeError as exc:
        raise TrainingError(
            f"Final training of {class_name} failed with params {filtered}: {exc}",
            code="final_training_error",
        ) from exc
    except ValueError as exc:
        # Invalid hyperparameter combinations (e.g. n_components > n_users)
        # surface as ValueError from irspack — also map to TrainingError so
        # the operator-visible exit code is 4 (training) rather than 1.
        raise TrainingError(
            f"Final training of {class_name} rejected params {filtered}: {exc}",
            code="final_training_error",
        ) from exc

    return IDMappedRecommender(recommender, uids_str, iids_str)
