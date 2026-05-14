from __future__ import annotations

import re
from datetime import date
from typing import ClassVar, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recotem.datasource.base import FetchContext

_EVENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


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
        raise NotImplementedError  # filled in Task 3.4

    def probe(self) -> None:
        raise NotImplementedError  # filled in Task 3.5

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        raise NotImplementedError  # filled in Task 3.6
