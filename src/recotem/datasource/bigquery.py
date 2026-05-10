"""BigQuerySource — google-cloud-bigquery with ADC and @parameter binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

import structlog
from pydantic import BaseModel

from recotem.datasource.base import DataSourceError, FetchContext

if TYPE_CHECKING:
    import pandas as pd

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class BigQueryConfig(BaseModel, extra="forbid"):
    """Configuration schema for BigQuery sources.

    ``query`` and ``query_parameters`` are intentionally excluded from env-var
    expansion (see loader.py ``_NO_EXPAND_KEYS``). BigQuery callers must use
    ``@param`` placeholders for dynamic values.
    """

    type: Literal["bigquery"] = "bigquery"
    query: str
    query_parameters: dict[str, Any] | None = None
    project: str | None = None


# ---------------------------------------------------------------------------
# Source class
# ---------------------------------------------------------------------------


class BigQuerySource:
    """Fetches data from BigQuery using Application Default Credentials (ADC).

    ``google-cloud-bigquery`` and ``db-dtypes`` are optional extras.  Their
    import is deferred to ``__init__`` so that the module can be imported on
    systems without the Google Cloud SDK.

    Query parameters are bound via BigQuery named parameters (``@name``).
    The following Python → BigQuery type mapping is used:

    - ``int`` / ``float`` → ``INT64`` / ``FLOAT64``
    - ``str`` → ``STRING``
    - ``bool`` → ``BOOL``

    All other types raise :class:`DataSourceError` at bind time.
    """

    type_name: ClassVar[str] = "bigquery"
    Config: ClassVar[type[BaseModel]] = BigQueryConfig
    extras_required: ClassVar[list[str]] = ["bigquery"]
    no_expand_fields: ClassVar[frozenset[str]] = frozenset(
        {"query", "query_parameters"}
    )

    def __init__(self, config: BigQueryConfig) -> None:
        try:
            import google.cloud.bigquery  # noqa: F401
        except ImportError as exc:
            raise DataSourceError(
                "google-cloud-bigquery is required for BigQuerySource. "
                "Install it with: pip install recotem[bigquery]"
            ) from exc
        try:
            import db_dtypes  # noqa: F401
        except ImportError as exc:
            raise DataSourceError(
                "db-dtypes is required for BigQuerySource. "
                "Install it with: pip install recotem[bigquery]"
            ) from exc
        self._config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_query_parameters(self) -> list:  # type: ignore[type-arg]
        """Convert ``query_parameters`` dict to BigQuery ``ScalarQueryParameter`` list."""
        from google.cloud.bigquery import ScalarQueryParameter

        params = self._config.query_parameters or {}
        bq_params = []
        for name, value in params.items():
            if isinstance(value, bool):
                bq_params.append(ScalarQueryParameter(name, "BOOL", value))
            elif isinstance(value, int):
                bq_params.append(ScalarQueryParameter(name, "INT64", value))
            elif isinstance(value, float):
                bq_params.append(ScalarQueryParameter(name, "FLOAT64", value))
            elif isinstance(value, str):
                bq_params.append(ScalarQueryParameter(name, "STRING", value))
            else:
                raise DataSourceError(
                    f"BigQuery query parameter '{name}' has unsupported type "
                    f"'{type(value).__name__}'. "
                    "Supported types: int, float, str, bool."
                )
        return bq_params

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def probe(self) -> None:
        """Verify ADC, client creation, and query validity via a dry-run job.

        Dry-run jobs are billed nothing and processed bytes is set without
        running the query, which makes them a cheap connectivity / auth /
        SQL-syntax probe for ``recotem validate``.

        Raises
        ------
        DataSourceError
            On ADC failure, network error, or invalid SQL / parameters.
        """
        from google.api_core.exceptions import GoogleAPICallError
        from google.cloud import bigquery

        cfg = self._config
        try:
            client = bigquery.Client(project=cfg.project)
        except Exception as exc:
            raise DataSourceError(
                f"BigQuery client creation failed: {exc}. "
                "Ensure Application Default Credentials (ADC) are configured."
            ) from exc

        job_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
        )
        if cfg.query_parameters:
            job_config.query_parameters = self._build_query_parameters()

        try:
            client.query(cfg.query, job_config=job_config)
        except GoogleAPICallError as exc:
            raise DataSourceError(f"BigQuery dry-run failed: {exc}") from exc
        except Exception as exc:
            raise DataSourceError(
                f"Unexpected error during BigQuery dry-run: {exc}"
            ) from exc

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Execute the BigQuery query and return results as a DataFrame.

        Uses the BigQuery Storage Read API (``create_bqstorage_client=True``)
        for fast downloads when the dependency is available; falls back to
        the REST API otherwise.

        Raises
        ------
        DataSourceError
            On authentication failure, invalid query, network error, or
            unsupported parameter type.
        """
        from google.api_core.exceptions import GoogleAPICallError
        from google.cloud import bigquery

        cfg = self._config
        logger.info(
            "bigquery_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            project=cfg.project,
        )

        try:
            client = bigquery.Client(project=cfg.project)
        except Exception as exc:
            raise DataSourceError(
                f"Failed to create BigQuery client: {exc}. "
                "Ensure Application Default Credentials (ADC) are configured."
            ) from exc

        job_config = bigquery.QueryJobConfig()
        if cfg.query_parameters:
            try:
                job_config.query_parameters = self._build_query_parameters()
            except DataSourceError:
                raise

        try:
            query_job = client.query(cfg.query, job_config=job_config)
        except GoogleAPICallError as exc:
            raise DataSourceError(f"BigQuery query submission failed: {exc}") from exc
        except Exception as exc:
            raise DataSourceError(
                f"Unexpected error submitting BigQuery query: {exc}"
            ) from exc

        # Attempt fast path via Storage Read API; fall back to REST API only
        # for expected, recoverable failures (missing extra, storage-specific
        # API errors).  All other exceptions propagate immediately so that OOM
        # errors, quota failures, and auth errors are not silently swallowed.
        try:
            try:
                df = query_job.to_dataframe(create_bqstorage_client=True)
            except ImportError as storage_exc:
                # google-cloud-bigquery-storage extra is not installed.
                logger.warning(
                    "bigquery_storage_fallback",
                    reason="ImportError — google-cloud-bigquery-storage not installed",
                    exc_type=type(storage_exc).__name__,
                    exc=str(storage_exc),
                )
                df = query_job.to_dataframe()
            except GoogleAPICallError as storage_exc:
                # Storage-specific API failure (PermissionDenied, ServiceUnavailable,
                # etc.) — fall back to the slower REST path and log the cause.
                logger.warning(
                    "bigquery_storage_fallback",
                    reason="GoogleAPICallError from Storage Read API",
                    exc_type=type(storage_exc).__name__,
                    exc=str(storage_exc),
                )
                df = query_job.to_dataframe()
            # Any other exception (MemoryError, RuntimeError, etc.) propagates
            # out of the inner try and is caught by the outer handler below.
        except GoogleAPICallError as exc:
            raise DataSourceError(f"BigQuery query execution failed: {exc}") from exc
        except Exception as exc:
            raise DataSourceError(
                f"Failed to download BigQuery results: {exc}"
            ) from exc

        logger.info(
            "bigquery_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            rows=len(df),
            columns=list(df.columns),
        )
        return df
