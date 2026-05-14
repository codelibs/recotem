from __future__ import annotations

from typing import ClassVar, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from recotem.datasource.base import FetchContext


class SQLConfig(BaseModel):
    type: Literal["sql"]
    dsn_env: str = Field(
        ...,
        min_length=1,
        pattern=r"^RECOTEM_RECIPE_[A-Z0-9_]+$",
    )
    query: str = Field(..., min_length=1)
    query_parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    connect_timeout_seconds: int = Field(10, ge=1, le=60)
    statement_timeout_seconds: int = Field(300, ge=1, le=1800)

    model_config = ConfigDict(extra="forbid")


class SQLSource:
    type_name: ClassVar[str] = "sql"
    Config: ClassVar[type[BaseModel]] = SQLConfig
    extras_required: ClassVar[list[str]] = ["sqlalchemy"]
    no_expand_fields: ClassVar[frozenset[str]] = frozenset({"query", "dsn_env"})

    def __init__(self, config: SQLConfig) -> None:
        raise NotImplementedError  # filled in Task 2.4

    def probe(self) -> None:
        raise NotImplementedError  # filled in Task 2.5

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        raise NotImplementedError  # filled in Task 2.6
