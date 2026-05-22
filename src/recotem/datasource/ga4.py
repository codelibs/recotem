from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, ClassVar, Literal

import pandas as pd
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recotem._metrics_ga4 import inc_ga4_pages, inc_ga4_rows, set_ga4_quota_remaining
from recotem.config import get_ga4_max_pages
from recotem.datasource.base import DataSourceError, FetchContext

_EVENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
_log = structlog.get_logger(__name__)

_PAGE_SIZE = 100_000


class GA4Config(BaseModel):
    type: Literal["ga4"]
    property_id: str = Field(..., pattern=r"^\d+$")
    user_dimension: Literal["userId", "userPseudoId"]
    item_dimension: str = Field("itemId", min_length=1, max_length=64)
    time_dimension: Literal["date", "dateHour", "dateHourMinute"] = "date"
    event_names: list[str] = Field(..., min_length=1, max_length=50)
    lookback_days: int | None = Field(None, ge=1, le=3650)
    start_date: date | None = None
    end_date: date | None = None
    max_rows: int = Field(..., ge=1, le=50_000_000)
    weight_column: str = "event_count"
    api_timeout_seconds: int = Field(60, ge=5, le=600)

    model_config = ConfigDict(extra="forbid")

    @field_validator("event_names")
    @classmethod
    def _valid_event_names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not _EVENT_NAME_RE.match(name):
                raise ValueError(
                    f"event name {name!r} does not match GA4 rule "
                    "^[A-Za-z_][A-Za-z0-9_]{0,39}$"
                )
        return v

    @model_validator(mode="after")
    def _date_range(self) -> GA4Config:
        rolling = self.lookback_days is not None
        fixed = self.start_date is not None or self.end_date is not None
        if rolling and fixed:
            raise ValueError(
                "set exactly one of lookback_days OR (start_date + end_date), not both"
            )
        if not rolling and not fixed:
            raise ValueError(
                "set exactly one of lookback_days OR (start_date + end_date)"
            )
        if fixed:
            if self.start_date is None or self.end_date is None:
                raise ValueError(
                    "fixed date range requires both start_date and end_date"
                )
            if self.start_date > self.end_date:
                raise ValueError("start_date must be <= end_date")
        return self

    @model_validator(mode="after")
    def _no_weight_column_collision(self) -> GA4Config:
        """Reject weight_column values that collide with dimension keys.

        The fetch loop builds each page record as a dict with one entry per
        dimension (``user_dimension`` / ``item_dimension`` / ``time_dimension``
        / the literal ``"eventName"`` key) plus the weight column.  If the
        weight column matches any of those names, the later dict-literal entry
        silently overwrites the earlier one — losing either a dimension value
        or the metric.  Reject up front at config-validation time so the
        problem surfaces as a clear ValidationError rather than as a missing
        or wrong column at fetch time.
        """
        reserved = {
            self.user_dimension,
            self.item_dimension,
            self.time_dimension,
            "eventName",
        }
        if self.weight_column in reserved:
            raise ValueError(
                f"weight_column={self.weight_column!r} collides with a dimension "
                f"name (reserved: {sorted(reserved)}); choose a different label."
            )
        return self


class GA4Source:
    type_name: ClassVar[str] = "ga4"
    Config: ClassVar[type[BaseModel]] = GA4Config
    extras_required: ClassVar[list[str]] = ["ga4"]
    no_expand_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: GA4Config) -> None:
        # Verify the library is installed at construction time so that a missing
        # extra produces a clear DataSourceError rather than an AttributeError
        # later.  Client construction is deferred to ``_get_client()`` so that
        # ``recotem validate`` (which instantiates sources) does not trigger
        # ADC resolution.
        try:
            from google.analytics.data_v1beta import (
                BetaAnalyticsDataClient,  # noqa: F401
            )
        except ImportError as exc:
            raise DataSourceError(
                "google-analytics-data is required for GA4Source. "
                "Install with: pip install 'recotem[ga4]'"
            ) from exc

        self._config = config
        self._client: Any = None  # constructed lazily in _get_client()
        _log.debug(
            "ga4_source_initialized",
            property_id=config.property_id,
            event_names=config.event_names,
        )

    def _get_client(self) -> Any:
        """Return the BetaAnalyticsDataClient, constructing it on first call."""
        if self._client is not None:
            return self._client

        from google.analytics.data_v1beta import BetaAnalyticsDataClient

        # Catch ADC-specific errors with a user-friendly message that does not
        # leak the ADC search path.  DefaultCredentialsError may not exist if
        # google-auth is not installed (unlikely in practice but handled
        # defensively).
        try:
            from google.auth.exceptions import DefaultCredentialsError as _DCE
        except ImportError:
            _DCE = None  # type: ignore[assignment,misc]

        _exc_types: tuple[type[Exception], ...] = (_DCE,) if _DCE is not None else ()

        try:
            self._client = BetaAnalyticsDataClient()
        except Exception as exc:
            if _exc_types and isinstance(exc, _exc_types):
                # Emit a debug-level structured event with the exception class
                # (no message — those can embed ADC search paths).  Operators
                # can switch to debug logging to disambiguate ADC failure modes
                # without the user-facing message leaking filesystem hints.
                _log.debug(
                    "ga4_adc_resolution_failed",
                    error_class=type(exc).__name__,
                )
                raise DataSourceError(
                    "ADC is not configured for the GA4 Data API. "
                    "See docs/data-sources/ga4.md for setup."
                ) from None  # suppress chain to avoid leaking ADC search paths
            raise DataSourceError(
                f"failed to construct BetaAnalyticsDataClient: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        return self._client

    def probe(self) -> None:
        try:
            from google.api_core.exceptions import GoogleAPICallError, PermissionDenied
        except ImportError as exc:
            raise DataSourceError("google.api_core is required for GA4Source") from exc

        client = self._get_client()
        request = self._build_request(limit=1, offset=0)
        try:
            client.run_report(
                request=request,
                retry=self._retry_policy(),
                timeout=float(self._config.api_timeout_seconds),
            )
        except PermissionDenied as exc:
            raise DataSourceError(
                f"GA4 access denied for property {self._config.property_id!r}; "
                "grant the service account roles/analytics.viewer on the property."
            ) from exc
        except GoogleAPICallError as exc:
            raise DataSourceError(
                f"GA4 probe failed: {type(exc).__name__}: {exc}"
            ) from exc

    def _retry_policy(self):
        from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable
        from google.api_core.retry import Retry, if_exception_type

        # ``timeout`` is the total retry *budget* (sum of all attempt wait
        # times).  We set it to 3× the per-attempt timeout so that up to ~3
        # full retry cycles can fire before giving up.  The per-attempt wall
        # time is controlled by the separate ``timeout=`` kwarg passed to
        # ``run_report``.
        return Retry(
            predicate=if_exception_type(ResourceExhausted, ServiceUnavailable),
            initial=1.0,
            maximum=30.0,
            multiplier=2.0,
            timeout=float(self._config.api_timeout_seconds) * 3.0,
        )

    def _build_request(self, *, limit: int, offset: int):
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Filter,
            FilterExpression,
            Metric,
            RunReportRequest,
        )

        start, end = self._date_range()
        return RunReportRequest(
            property=f"properties/{self._config.property_id}",
            dimensions=[
                Dimension(name=self._config.user_dimension),
                Dimension(name=self._config.item_dimension),
                Dimension(name=self._config.time_dimension),
                Dimension(name="eventName"),
            ],
            metrics=[Metric(name="eventCount")],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimension_filter=FilterExpression(
                filter=Filter(
                    field_name="eventName",
                    in_list_filter=Filter.InListFilter(
                        values=list(self._config.event_names)
                    ),
                )
            ),
            limit=limit,
            offset=offset,
            return_property_quota=True,
        )

    def _date_range(self) -> tuple[str, str]:
        if self._config.lookback_days is not None:
            return (f"{self._config.lookback_days}daysAgo", "yesterday")
        assert self._config.start_date is not None
        assert self._config.end_date is not None
        return (
            self._config.start_date.isoformat(),
            self._config.end_date.isoformat(),
        )

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        try:
            from google.api_core.exceptions import GoogleAPICallError, PermissionDenied
        except ImportError as exc:
            raise DataSourceError("google.api_core is required for GA4Source") from exc

        client = self._get_client()
        page_size = _PAGE_SIZE
        max_pages = get_ga4_max_pages()
        page_frames: list[pd.DataFrame] = []
        offset = 0
        total_rows_accumulated = 0
        # Track whether we've already complained about an entirely-empty
        # property_quota response.  Without this, very long fetches would
        # emit one WARNING per page (potentially hundreds), drowning out
        # actionable signals in the log stream.
        quota_warning_emitted = False

        # Per-fetch wall-clock budget: 10× the per-attempt timeout.  At the
        # default api_timeout=60 s that is 10 minutes; the budget scales with
        # configured timeouts.  This prevents runaway loops under sustained
        # ResourceExhausted back-pressure across hundreds of pages.
        deadline_wall = time.monotonic() + float(self._config.api_timeout_seconds) * 10

        for page_idx in range(max_pages):
            if time.monotonic() > deadline_wall:
                raise DataSourceError(
                    f"GA4 fetch exceeded total wall-clock budget of "
                    f"{self._config.api_timeout_seconds * 10}s on page {page_idx}"
                )

            request = self._build_request(limit=page_size, offset=offset)
            try:
                response = client.run_report(
                    request=request,
                    retry=self._retry_policy(),
                    timeout=float(self._config.api_timeout_seconds),
                )
            except PermissionDenied as exc:
                raise DataSourceError(
                    f"GA4 access denied for property {self._config.property_id!r}; "
                    "grant roles/analytics.viewer."
                ) from exc
            except GoogleAPICallError as exc:
                raise DataSourceError(
                    f"GA4 fetch failed on page {page_idx}: {type(exc).__name__}: {exc}"
                ) from exc

            # Re-check the wall-clock budget after the SDK's own Retry policy
            # may have consumed several seconds on this page.  Without this
            # post-call check, an unlucky page that exhausts the per-call
            # ``Retry(timeout=api_timeout*3)`` budget could overshoot the
            # outer ``deadline_wall`` by up to one full retry cycle before
            # the next iteration's top-of-loop check fires.
            if time.monotonic() > deadline_wall:
                raise DataSourceError(
                    f"GA4 fetch exceeded total wall-clock budget of "
                    f"{self._config.api_timeout_seconds * 10}s after page {page_idx}"
                )

            # Warn if the GA4 backend omitted rows due to cardinality limits.
            metadata = getattr(response, "metadata", None)
            if metadata is not None and getattr(
                metadata, "data_loss_from_other_row", False
            ):
                _log.warning(
                    "ga4_data_loss_from_other_row",
                    recipe=ctx.recipe_name,
                    page=page_idx,
                )

            inc_ga4_pages(ctx.recipe_name)
            if self._record_quota(ctx.recipe_name, response, quota_warning_emitted):
                quota_warning_emitted = True

            page_rows = list(response.rows or [])
            page_records = []
            for row in page_rows:
                if len(row.dimension_values) < 4 or len(row.metric_values) < 1:
                    raise DataSourceError(
                        f"unexpected GA4 row shape on page {page_idx}: got "
                        f"{len(row.dimension_values)} dimensions and "
                        f"{len(row.metric_values)} metrics (expected 4 and 1); "
                        "SDK version may be incompatible"
                    )
                page_records.append(
                    {
                        self._config.user_dimension: row.dimension_values[0].value,
                        self._config.item_dimension: row.dimension_values[1].value,
                        self._config.time_dimension: row.dimension_values[2].value,
                        "eventName": row.dimension_values[3].value,
                        self._config.weight_column: row.metric_values[0].value,
                    }
                )
            if page_records:
                page_frames.append(pd.DataFrame.from_records(page_records))
            del page_records

            inc_ga4_rows(ctx.recipe_name, len(page_rows))
            total_rows_accumulated += len(page_rows)

            if total_rows_accumulated > self._config.max_rows:
                raise DataSourceError(
                    f"GA4 result exceeds max_rows={self._config.max_rows}; "
                    "narrow the date range or event filter"
                )

            if len(page_rows) < page_size:
                # Short page (including empty) means end of result set.
                break

            offset += page_size
        else:
            # Completed max_pages full pages without seeing a short page.
            raise DataSourceError(
                f"GA4 fetch reached max_pages={max_pages} without seeing a short "
                f"page; increase RECOTEM_GA4_MAX_PAGES or tighten the query"
            )

        # Build result DataFrame — always include expected columns even when empty.
        expected_columns = [
            self._config.user_dimension,
            self._config.item_dimension,
            self._config.time_dimension,
            self._config.weight_column,
        ]
        if page_frames:
            df = pd.concat(page_frames, ignore_index=True)
            df = df.drop(columns=["eventName"])
        else:
            df = pd.DataFrame(columns=expected_columns)

        if self._config.weight_column in df.columns:
            try:
                numeric = pd.to_numeric(df[self._config.weight_column], errors="raise")
            except (ValueError, TypeError) as exc:
                # pandas raises ValueError on non-numeric strings.  Surface as
                # DataSourceError so the CLI exits 3 (_EXIT_DATASOURCE) instead
                # of 1 (_EXIT_UNKNOWN); the GA4 SDK shouldn't emit non-numeric
                # eventCount but a regression there should still be classified.
                raise DataSourceError(
                    f"GA4 eventCount could not be parsed as numeric for property "
                    f"{self._config.property_id!r}: {type(exc).__name__}"
                ) from exc
            if not (numeric % 1 == 0).all():
                offender = numeric[numeric % 1 != 0].iloc[0]
                raise DataSourceError(
                    f"GA4 eventCount contains non-integer value {offender!r} for "
                    f"property {self._config.property_id!r}; expected integer metric"
                )
            df[self._config.weight_column] = numeric.astype("int64")

        _log.info(
            "ga4_fetch_complete",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            property_id=self._config.property_id,
            rows_loaded=len(df),
        )
        return df

    def _record_quota(
        self, recipe: str, response: Any, warning_already_emitted: bool = False
    ) -> bool:
        """Record quota gauges; return True if an all-missing warning fired now.

        ``warning_already_emitted`` lets the caller suppress repeated
        ``ga4_quota_all_attrs_missing`` warnings on subsequent pages of the
        same fetch — for long-running paginated fetches the warning was
        previously emitted on every page and would drown out actionable
        log signals.  The return value tells the caller whether *this*
        invocation just emitted the warning, so it can flip its own flag.
        """
        quota = getattr(response, "property_quota", None)
        if quota is None:
            return False
        parsed_count = 0
        for attr in (
            "tokens_per_hour",
            "tokens_per_day",
            "concurrent_requests",
            "server_errors_per_project_per_hour",
        ):
            q = getattr(quota, attr, None)
            if q is None:
                continue
            remaining = getattr(q, "remaining", None)
            if remaining is None:
                continue
            try:
                set_ga4_quota_remaining(recipe, attr, float(remaining))  # type: ignore[arg-type]
                parsed_count += 1
            except (TypeError, ValueError) as exc:
                _log.warning(
                    "ga4_quota_parse_failed",
                    attr=attr,
                    error=type(exc).__name__,
                )
        if parsed_count == 0 and not warning_already_emitted:
            _log.warning("ga4_quota_all_attrs_missing", recipe=recipe)
            return True
        return False
