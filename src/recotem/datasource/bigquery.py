"""BigQuerySource — google-cloud-bigquery with ADC and @parameter binding."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from pydantic import BaseModel, Field

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

    type: str = Field(default="bigquery", pattern=r"^bigquery$")
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
            raise DataSourceError(
                f"BigQuery query submission failed: {exc}"
            ) from exc
        except Exception as exc:
            raise DataSourceError(
                f"Unexpected error submitting BigQuery query: {exc}"
            ) from exc

        # Attempt fast path via Storage Read API; fall back to standard.
        try:
            try:
                df = query_job.to_dataframe(create_bqstorage_client=True)
            except Exception:
                df = query_job.to_dataframe()
        except GoogleAPICallError as exc:
            raise DataSourceError(
                f"BigQuery query execution failed: {exc}"
            ) from exc
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
