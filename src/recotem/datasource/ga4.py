from __future__ import annotations

import re
from datetime import date
from typing import ClassVar, Literal

import pandas as pd
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recotem._metrics_ga4 import inc_ga4_pages, inc_ga4_rows, set_ga4_quota_remaining
from recotem.config import get_ga4_max_pages
from recotem.datasource.base import DataSourceError, FetchContext

_EVENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
_log = structlog.get_logger(__name__)


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


class GA4Source:
    type_name: ClassVar[str] = "ga4"
    Config: ClassVar[type[BaseModel]] = GA4Config
    extras_required: ClassVar[list[str]] = ["google-analytics-data"]
    no_expand_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: GA4Config) -> None:
        try:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
        except ImportError as exc:
            raise DataSourceError(
                "google-analytics-data is required for GA4Source. "
                "Install with: pip install 'recotem[ga4]'"
            ) from exc

        try:
            self._client = BetaAnalyticsDataClient()
        except Exception as exc:
            raise DataSourceError(
                "failed to construct BetaAnalyticsDataClient — confirm ADC "
                "is configured (GOOGLE_APPLICATION_CREDENTIALS or "
                "Workload Identity)."
            ) from exc

        self._config = config
        _log.debug(
            "ga4_source_initialized",
            property_id=config.property_id,
            event_names=config.event_names,
        )

    def probe(self) -> None:
        try:
            from google.api_core.exceptions import GoogleAPICallError, PermissionDenied
        except ImportError as exc:
            raise DataSourceError("google.api_core is required for GA4Source") from exc

        request = self._build_request(limit=1, offset=0)
        try:
            self._client.run_report(request=request, retry=self._retry_policy())
        except PermissionDenied as exc:
            raise DataSourceError(
                f"GA4 access denied for property {self._config.property_id!r}; "
                "grant the service account roles/analytics.viewer on the property."
            ) from exc
        except GoogleAPICallError as exc:
            raise DataSourceError(f"GA4 probe failed: {type(exc).__name__}") from exc

    def _retry_policy(self):
        from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable
        from google.api_core.retry import Retry, if_exception_type

        return Retry(
            predicate=if_exception_type(ResourceExhausted, ServiceUnavailable),
            initial=1.0,
            maximum=30.0,
            multiplier=2.0,
            deadline=float(self._config.api_timeout_seconds),
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

        page_size = 100_000
        max_pages = get_ga4_max_pages()
        accumulated: list[dict[str, object]] = []
        offset = 0
        retry_policy = self._retry_policy()

        for page_idx in range(max_pages):
            request = self._build_request(limit=page_size, offset=offset)
            try:
                response = self._client.run_report(request=request, retry=retry_policy)
            except PermissionDenied as exc:
                raise DataSourceError(
                    f"GA4 access denied for property {self._config.property_id!r}; "
                    "grant roles/analytics.viewer."
                ) from exc
            except GoogleAPICallError as exc:
                raise DataSourceError(
                    f"GA4 fetch failed on page {page_idx}: {type(exc).__name__}"
                ) from exc

            inc_ga4_pages(ctx.recipe_name)
            self._record_quota(ctx.recipe_name, response)

            page_rows = list(response.rows or [])
            for row in page_rows:
                dv = [d.value for d in row.dimension_values]
                mv = row.metric_values[0].value
                accumulated.append(
                    {
                        self._config.user_dimension: dv[0],
                        self._config.item_dimension: dv[1],
                        self._config.time_dimension: dv[2],
                        "eventName": dv[3],
                        self._config.weight_column: mv,
                    }
                )
            inc_ga4_rows(ctx.recipe_name, len(page_rows))

            if len(accumulated) > self._config.max_rows:
                raise DataSourceError(
                    f"GA4 result exceeds max_rows={self._config.max_rows}; "
                    "narrow the date range or event filter"
                )

            total_remote = int(getattr(response, "row_count", 0) or 0)
            if not page_rows:
                break
            if total_remote and len(accumulated) >= total_remote:
                break
            offset += page_size
        else:
            raise DataSourceError(
                f"GA4 fetch exceeded RECOTEM_GA4_MAX_PAGES={max_pages}; "
                "tighten the query or raise the cap"
            )

        df = pd.DataFrame.from_records(accumulated)
        if "eventName" in df.columns:
            df = df.drop(columns=["eventName"])
        if self._config.weight_column in df.columns:
            df[self._config.weight_column] = df[self._config.weight_column].astype(
                "int64"
            )
        _log.info(
            "ga4_fetch_complete",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            property_id=self._config.property_id,
            rows_loaded=len(df),
        )
        return df

    def _record_quota(self, recipe: str, response) -> None:
        quota = getattr(response, "property_quota", None)
        if quota is None:
            return
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
                set_ga4_quota_remaining(recipe, attr, float(remaining))
            except (TypeError, ValueError):
                pass
