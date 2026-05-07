"""CSVSource and ParquetSource — fsspec-backed via pandas."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog
from pydantic import BaseModel, Field

from recotem.datasource.base import DataSourceError, FetchContext

if TYPE_CHECKING:
    import pandas as pd

logger = structlog.get_logger(__name__)


class CSVConfig(BaseModel, extra="forbid"):
    """Configuration schema for CSV sources."""

    type: str = Field(default="csv", pattern=r"^csv$")
    path: str
    delimiter: str = ","
    encoding: str = "utf-8"
    header: int = 0
    dtype: dict[str, str] | None = None


class CSVSource:
    """Reads a CSV file using pandas + fsspec.

    Supports local paths, ``s3://``, ``gs://``, ``az://``, and transparent
    gzip/zip compression as detected by pandas.

    Optional imports are deferred to ``__init__`` so that this module can be
    imported even if pandas / fsspec are not installed.
    """

    type_name: ClassVar[str] = "csv"
    Config: ClassVar[type[BaseModel]] = CSVConfig
    extras_required: ClassVar[list[str]] = []

    def __init__(self, config: CSVConfig) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError as exc:
            raise DataSourceError(
                "pandas is required for CSVSource. Install it with: pip install recotem"
            ) from exc
        self._config = config

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Fetch the CSV file and return a DataFrame.

        Raises
        ------
        DataSourceError
            On any I/O, parse, or schema error.
        """
        import pandas as pd

        cfg = self._config
        logger.info(
            "csv_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=cfg.path,
        )

        read_kwargs: dict = {
            "sep": cfg.delimiter,
            "encoding": cfg.encoding,
            "header": cfg.header,
        }
        if cfg.dtype:
            read_kwargs["dtype"] = cfg.dtype

        try:
            df: pd.DataFrame = pd.read_csv(cfg.path, **read_kwargs)
        except FileNotFoundError as exc:
            raise DataSourceError(f"CSV file not found: {cfg.path}") from exc
        except PermissionError as exc:
            raise DataSourceError(
                f"Permission denied reading CSV file: {cfg.path}"
            ) from exc
        except Exception as exc:
            raise DataSourceError(
                f"Failed to read CSV from '{cfg.path}': {exc}"
            ) from exc

        if df.empty:
            raise DataSourceError(
                f"CSV file '{cfg.path}' is empty (no data rows after header)."
            )

        logger.info(
            "csv_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            rows=len(df),
            columns=list(df.columns),
        )
        return df


class ParquetConfig(BaseModel, extra="forbid"):
    """Configuration schema for Parquet sources."""

    type: str = Field(default="parquet", pattern=r"^parquet$")
    path: str


class ParquetSource:
    """Reads a Parquet file using pandas + fsspec.

    Supports local paths, ``s3://``, ``gs://``, and ``az://``.
    Optional imports are deferred to ``__init__``.
    """

    type_name: ClassVar[str] = "parquet"
    Config: ClassVar[type[BaseModel]] = ParquetConfig
    extras_required: ClassVar[list[str]] = []

    def __init__(self, config: ParquetConfig) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError as exc:
            raise DataSourceError(
                "pandas is required for ParquetSource. "
                "Install it with: pip install recotem"
            ) from exc
        self._config = config

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Fetch the Parquet file and return a DataFrame.

        Raises
        ------
        DataSourceError
            On any I/O or parse error.
        """
        import pandas as pd

        cfg = self._config
        logger.info(
            "parquet_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=cfg.path,
        )

        try:
            df: pd.DataFrame = pd.read_parquet(cfg.path)
        except FileNotFoundError as exc:
            raise DataSourceError(f"Parquet file not found: {cfg.path}") from exc
        except PermissionError as exc:
            raise DataSourceError(
                f"Permission denied reading Parquet file: {cfg.path}"
            ) from exc
        except Exception as exc:
            raise DataSourceError(
                f"Failed to read Parquet from '{cfg.path}': {exc}"
            ) from exc

        logger.info(
            "parquet_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            rows=len(df),
            columns=list(df.columns),
        )
        return df
