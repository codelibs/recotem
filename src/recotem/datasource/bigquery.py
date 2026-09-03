"""BigQuerySource — google-cloud-bigquery with ADC and @parameter binding."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import structlog
from pydantic import BaseModel

from recotem._metrics_bigquery import inc_bigquery_storage_fallback
from recotem.config import is_truthy_env
from recotem.datasource.base import DataSourceError, FetchContext

if TYPE_CHECKING:
    import pandas as pd

logger = structlog.get_logger(__name__)

_STORAGE_EXTRA_HINT = (
    "Install it with: pip install recotem[bigquery] "
    "(includes google-cloud-bigquery-storage)."
)


def _storage_api_available() -> bool:
    """Return True when ``google-cloud-bigquery-storage`` can be imported.

    ``google-cloud-bigquery`` never raises when the Storage Read API dependency
    is absent, so the capability has to be probed explicitly rather than
    inferred from an exception:

    * ``RowIterator._should_use_bqstorage`` catches its own
      ``BigQueryStorageNotFoundError``, emits a ``UserWarning``, and returns
      ``False``.
    * ``Client._ensure_bqstorage_client`` does the same and returns ``None``.

    Both then download over REST transparently.  Waiting for an ``ImportError``
    that the library does not raise is what made
    ``RECOTEM_BQ_REQUIRE_STORAGE_API`` silently unenforceable when the extra was
    missing: strict mode was requested, the REST path ran anyway, and the run
    reported success.
    """
    try:
        import google.cloud.bigquery_storage  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _assert_storage_api_capability() -> None:
    """Enforce ``RECOTEM_BQ_REQUIRE_STORAGE_API``'s dependency precondition.

    Shared by ``probe()`` and ``fetch()``.  It is a pure import check that
    needs no query and no credentials, so ``recotem validate`` can answer it
    for free — leaving it to ``fetch()`` alone meant validate green-lit a
    configuration that ``train`` then refused on identical config and env.
    """
    if not is_truthy_env(os.environ.get("RECOTEM_BQ_REQUIRE_STORAGE_API")):
        return
    if _storage_api_available():
        return
    raise DataSourceError(
        "RECOTEM_BQ_REQUIRE_STORAGE_API is set but "
        "google-cloud-bigquery-storage is not installed, so the "
        "BigQuery Storage Read API cannot be used. " + _STORAGE_EXTRA_HINT
    )


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
            On a strict-mode dependency violation, ADC failure, network error,
            or invalid SQL / parameters.
        """
        from google.api_core.exceptions import GoogleAPICallError
        from google.cloud import bigquery

        # Checked first: a strict-mode violation needs no client, no
        # credentials, and no round trip, and it is what ``fetch()`` would
        # refuse on anyway.
        _assert_storage_api_capability()

        cfg = self._config
        try:
            client = bigquery.Client(project=cfg.project)
        except (MemoryError, RecursionError):
            raise
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
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise DataSourceError(
                f"Unexpected error during BigQuery dry-run: {exc}"
            ) from exc

    def _download_via_rest(self, query_job: Any) -> pd.DataFrame:
        """Re-download the finished job's rows over the REST API.

        A ``RowIterator`` is single-use.  ``RowIterator.to_dataframe`` builds
        its REST-download callable with ``iter(self.pages)``, and the ``pages``
        property flips ``_started`` **before** the Storage-vs-REST decision is
        taken — so the iterator handed to a failed Storage Read API attempt is
        already consumed, and reusing it raises
        ``ValueError("Iterator has already started")``.

        Asking the job for a fresh iterator is the safe move: the query is
        already ``DONE`` at this point, so this is a metadata read plus a
        result download, never a re-execution or a second scan.
        """
        try:
            return query_job.result().to_dataframe(create_bqstorage_client=False)
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise DataSourceError(
                f"BigQuery REST download failed after the Storage Read API "
                f"fallback: {exc}"
            ) from exc

    def _recover_from_storage_failure(
        self,
        query_job: Any,
        storage_exc: Exception,
        *,
        strict: bool,
    ) -> pd.DataFrame:
        """Decide what to do about a Storage Read API *transport* failure.

        Only reached once the query itself has completed successfully, so
        everything handled here really is a fast-transport problem and the
        Storage framing (and the ``bigquery.readSessions.create`` advice) is
        accurate.

        Branches on subclass:

        * PermissionDenied / Forbidden (403): IAM-only failure — the service
          account lacks ``bigquery.readSessions.create``.  REST fallback is
          safe and recommended; operators can grant the permission later to
          restore Storage-API throughput.
        * Anything else (ResourceExhausted 429 quota, ServiceUnavailable 503,
          RetryError, etc.): the REST path hits the same project quota /
          region, so falling back would double-bill or repeat the failure.
          Fail fast so operators see the genuine root cause instead of a slow
          second attempt with the same outcome.

        ``RECOTEM_BQ_REQUIRE_STORAGE_API=1`` refuses the fallback even for the
        PermissionDenied case.
        """
        if strict:
            raise DataSourceError(
                f"BigQuery Storage Read API failed and "
                "RECOTEM_BQ_REQUIRE_STORAGE_API is set — no REST "
                "fallback. Grant bigquery.readSessions.create on the "
                f"project to fix this. Original error: {storage_exc}"
            ) from storage_exc

        # Detect IAM-shaped failures by class name AND by message content so
        # the check works under three regimes:
        #
        # 1. Real production:  storage_exc is
        #    ``google.api_core.exceptions.PermissionDenied`` / ``Forbidden``
        #    -- class-name match fires.
        # 2. Test mocks that subclass our fake GoogleAPICallError but spell
        #    the message ``PERMISSION_DENIED: ...`` -- the message-substring
        #    fallback fires.
        # 3. Genuine non-IAM (Quota 429, 5xx, RetryError, etc.) -- no
        #    fallback; raise so the operator sees the real root cause and does
        #    not get double-billed on the REST path.
        exc_name = type(storage_exc).__name__
        iam_class_shapes = ("PermissionDenied", "Forbidden")
        iam_message_markers = (
            "PERMISSION_DENIED",
            "Forbidden",
            "permission denied",
        )

        # Determine IAM detection path for observability — checked in priority
        # order so the most reliable signal wins:
        # 1. HTTP status code (most objective, avoids string parsing).
        # 2. Exception class name exact match.
        # 3. MRO inheritance chain.
        # 4. Message-string marker substring (weakest — keep as last resort
        #    for test mocks that cannot subclass the real exception).
        _http_code = getattr(storage_exc, "code", None) or getattr(
            getattr(storage_exc, "response", None), "status_code", None
        )
        if _http_code == 403:
            is_iam_failure = True
            iam_detected_via = "http_403"
        elif exc_name in iam_class_shapes:
            is_iam_failure = True
            iam_detected_via = "class"
        elif any(
            base.__name__ in iam_class_shapes for base in type(storage_exc).__mro__
        ):
            is_iam_failure = True
            iam_detected_via = "mro"
        elif any(marker in str(storage_exc) for marker in iam_message_markers):
            is_iam_failure = True
            iam_detected_via = "message"
        else:
            is_iam_failure = False
            iam_detected_via = None

        if not is_iam_failure:
            # Non-IAM failure: quota, transient 5xx, etc.  REST would hit the
            # same constraint.  Surface as DataSourceError.
            inc_bigquery_storage_fallback("non_iam_error_no_fallback")
            raise DataSourceError(
                f"BigQuery Storage Read API failed with "
                f"{exc_name}: {storage_exc}. "
                "The query itself completed — this is a result-download "
                "failure. REST fallback skipped because the failure is not "
                "IAM-only — quota / 5xx / connectivity errors would "
                "recur on the REST path and inflate cost.  Set "
                "RECOTEM_BQ_REQUIRE_STORAGE_API=1 to surface this "
                "error unconditionally."
            ) from storage_exc

        logger.warning(
            "bigquery_storage_fallback",
            reason="PermissionDenied from Storage Read API — "
            "grant bigquery.readSessions.create to restore fast "
            "path; set RECOTEM_BQ_REQUIRE_STORAGE_API=1 to "
            "disable fallback",
            iam_permission_needed="bigquery.readSessions.create",
            iam_detected_via=iam_detected_via,
            exc_type=exc_name,
            exc=str(storage_exc),
        )
        # Counter label retained as ``api_error`` for backward compatibility
        # with existing dashboards / alerts.
        inc_bigquery_storage_fallback("api_error")
        return self._download_via_rest(query_job)

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Execute the BigQuery query and return results as a DataFrame.

        The query execution and the result download are two separate phases
        with two separate failure domains, and they are reported separately:

        * ``query_job.result()`` waits for the query to run.  Bad SQL, a
          missing table, a table-level permission denial, or a query quota
          failure surfaces here as ``BigQuery query execution failed: ...``.
        * ``RowIterator.to_dataframe()`` downloads the rows, preferring the
          BigQuery Storage Read API when ``google-cloud-bigquery-storage`` is
          installed and falling back to REST otherwise.  Only failures from
          this phase get the Storage Read API framing and the
          ``bigquery.readSessions.create`` advice.

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
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise DataSourceError(
                f"Failed to create BigQuery client: {exc}. "
                "Ensure Application Default Credentials (ADC) are configured."
            ) from exc

        # Strict mode is a *capability* precondition, so it is checked before
        # the query is submitted: refusing here costs nothing, whereas
        # refusing after ``result()`` would bill the scan and then throw the
        # rows away.  Capability, not exception, decides: google-cloud-bigquery
        # warns and silently downloads over REST when the storage dependency is
        # absent (see ``_storage_api_available``), so waiting for an error would
        # leave strict mode doing nothing at all.
        _assert_storage_api_capability()
        strict_storage_api = is_truthy_env(
            os.environ.get("RECOTEM_BQ_REQUIRE_STORAGE_API")
        )
        storage_api_available = _storage_api_available()

        job_config = bigquery.QueryJobConfig()
        if cfg.query_parameters:
            job_config.query_parameters = self._build_query_parameters()

        try:
            query_job = client.query(cfg.query, job_config=job_config)
        except GoogleAPICallError as exc:
            raise DataSourceError(f"BigQuery query submission failed: {exc}") from exc
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise DataSourceError(
                f"Unexpected error submitting BigQuery query: {exc}"
            ) from exc

        # ------------------------------------------------------------------
        # Phase 1 — run the query.
        #
        # ``client.query()`` returns as soon as the job is *submitted*; the
        # query has not executed yet.  Waiting for it explicitly here, instead
        # of letting ``to_dataframe()`` do it implicitly, keeps execution
        # failures (bad SQL, missing table, table-level permission denied,
        # query quota) out of the Storage Read API handler in phase 2.  While
        # the two phases were fused, *every* execution failure was reported as
        # "BigQuery Storage Read API failed with ..." and, under strict mode,
        # was answered with "Grant bigquery.readSessions.create" — advice that
        # cannot fix a typo in the SQL.
        # ------------------------------------------------------------------
        try:
            row_iterator = query_job.result()
        except DataSourceError:
            raise
        except GoogleAPICallError as exc:
            raise DataSourceError(f"BigQuery query execution failed: {exc}") from exc
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            raise DataSourceError(
                f"BigQuery query execution failed: {type(exc).__name__}: {exc}"
            ) from exc

        # ------------------------------------------------------------------
        # Phase 2 — download the result rows.
        #
        # Prefer the Storage Read API; fall back to REST only for expected,
        # recoverable transport failures.  All other exceptions propagate so
        # that OOM errors are not silently swallowed.  Strict mode already
        # refused a missing dependency before the query was submitted.
        # ------------------------------------------------------------------
        if not storage_api_available:
            logger.warning(
                "bigquery_storage_fallback",
                reason="google-cloud-bigquery-storage is not installed — "
                "downloading over the REST API; set "
                "RECOTEM_BQ_REQUIRE_STORAGE_API=1 to make this an error",
                recipe=ctx.recipe_name,
                run_id=ctx.run_id,
            )
            inc_bigquery_storage_fallback("missing_extra")
            try:
                df = row_iterator.to_dataframe(create_bqstorage_client=False)
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to download BigQuery results over the REST API: {exc}"
                ) from exc
        else:
            try:
                df = row_iterator.to_dataframe(create_bqstorage_client=True)
            except DataSourceError:
                raise
            except GoogleAPICallError as storage_exc:
                df = self._recover_from_storage_failure(
                    query_job, storage_exc, strict=strict_storage_api
                )
            except (MemoryError, RecursionError):
                raise
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
