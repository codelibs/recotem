"""CSVSource and ParquetSource — fsspec-backed via pandas."""

from __future__ import annotations

import hashlib
import hmac
from io import BytesIO
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse, urlunparse

import structlog
from pydantic import BaseModel, Field

from recotem.datasource.base import DataSourceError, FetchContext

if TYPE_CHECKING:
    import pandas as pd

logger = structlog.get_logger(__name__)

_NETWORK_SCHEMES: frozenset[str] = frozenset({"http", "https"})

_COMPRESSION_MAP: dict[str, str] = {
    ".gz": "gzip",
    ".bz2": "bz2",
    ".zip": "zip",
    ".xz": "xz",
}

# Schemes where urlparse's "userinfo" actually means credentials. For other
# schemes (gs://bucket@project/..., s3://...) the @ is part of the path.
_USERINFO_SCHEMES: frozenset[str] = frozenset({"http", "https", "ftp", "ftps"})


def _redact_url_userinfo(path: str) -> str:
    """Strip any userinfo from URL-shaped *path* before logging.

    Only redacts for HTTP(S) / FTP(S); object-store schemes like
    ``gs://bucket@project/...`` use ``@`` in their idiomatic syntax.
    """
    parsed = urlparse(path)
    if parsed.scheme.lower() not in _USERINFO_SCHEMES:
        return path
    if not parsed.username and not parsed.password:
        return path
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _infer_compression(path: str) -> str | None:
    """Pandas-style compression hint from path extension. Returns None if plain."""
    lower_path = urlparse(path).path.lower() if "://" in path else path.lower()
    for ext, codec in _COMPRESSION_MAP.items():
        if lower_path.endswith(ext):
            return codec
    return None


def _verify_sha256(actual: bytes, expected_hex: str) -> None:
    """hmac.compare_digest the sha256 of *actual* vs *expected_hex*."""
    digest = hashlib.sha256(actual).hexdigest()
    if not hmac.compare_digest(digest, expected_hex):
        # Show only first 8 chars on each side to avoid leaking ground truth.
        raise DataSourceError(
            f"sha256 mismatch: got {digest[:8]}…, expected {expected_hex[:8]}…"
        )


class CSVConfig(BaseModel, extra="forbid"):
    """Configuration schema for CSV sources."""

    type: str = Field(default="csv", pattern=r"^csv$")
    path: str
    delimiter: str = ","
    encoding: str = "utf-8"
    header: int = 0
    dtype: dict[str, str] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


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

    def probe(self) -> None:
        """Verify the CSV file exists and is readable without loading it."""
        _probe_fsspec_path(self._config.path, kind="CSV")

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Fetch the CSV file and return a DataFrame.

        Raises
        ------
        DataSourceError
            On any I/O, parse, or schema error.
        """
        import fsspec
        import pandas as pd

        cfg = self._config
        scheme = urlparse(cfg.path).scheme.lower()
        is_network = scheme in _NETWORK_SCHEMES
        safe_path = _redact_url_userinfo(cfg.path)

        logger.info(
            "csv_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            scheme=scheme or "local",
        )

        if is_network:
            # Implemented in Task 6; raise here so callers get a clear error
            # if they try to use the partial implementation.
            raise DataSourceError(
                f"Network-scheme CSV fetch is not yet wired for '{safe_path}'."
            )

        # Non-network path
        if cfg.sha256 is not None:
            try:
                with fsspec.open(cfg.path, "rb") as f:
                    raw_bytes = f.read()
            except FileNotFoundError as exc:
                raise DataSourceError(f"CSV file not found: {safe_path}") from exc
            except PermissionError as exc:
                raise DataSourceError(
                    f"Permission denied reading CSV file: {safe_path}"
                ) from exc
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read CSV from '{safe_path}': {exc}"
                ) from exc
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True
            compression = _infer_compression(cfg.path)
            read_kwargs: dict[str, object] = {
                "sep": cfg.delimiter,
                "encoding": cfg.encoding,
                "header": cfg.header,
                "compression": compression,
            }
            if cfg.dtype:
                read_kwargs["dtype"] = cfg.dtype
            try:
                df: pd.DataFrame = pd.read_csv(BytesIO(raw_bytes), **read_kwargs)
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to parse CSV from '{safe_path}': {exc}"
                ) from exc
            bytes_count = len(raw_bytes)
        else:
            sha256_verified = False
            read_kwargs = {
                "sep": cfg.delimiter,
                "encoding": cfg.encoding,
                "header": cfg.header,
            }
            if cfg.dtype:
                read_kwargs["dtype"] = cfg.dtype
            try:
                df = pd.read_csv(cfg.path, **read_kwargs)
            except FileNotFoundError as exc:
                raise DataSourceError(f"CSV file not found: {safe_path}") from exc
            except PermissionError as exc:
                raise DataSourceError(
                    f"Permission denied reading CSV file: {safe_path}"
                ) from exc
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read CSV from '{safe_path}': {exc}"
                ) from exc
            bytes_count = -1  # not measured on the streaming path

        if df.empty:
            raise DataSourceError(
                f"CSV file '{safe_path}' is empty (no data rows after header)."
            )

        logger.info(
            "csv_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            rows=len(df),
            bytes=bytes_count,
            sha256_verified=sha256_verified,
            columns=list(df.columns),
        )
        return df


class ParquetConfig(BaseModel, extra="forbid"):
    """Configuration schema for Parquet sources."""

    type: str = Field(default="parquet", pattern=r"^parquet$")
    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


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

    def probe(self) -> None:
        """Verify the Parquet file exists and is readable without loading it."""
        _probe_fsspec_path(self._config.path, kind="Parquet")

    def fetch(self, ctx: FetchContext) -> pd.DataFrame:
        """Fetch the Parquet file and return a DataFrame.

        Raises
        ------
        DataSourceError
            On any I/O or parse error.
        """
        import fsspec
        import pandas as pd

        cfg = self._config
        scheme = urlparse(cfg.path).scheme.lower()
        is_network = scheme in _NETWORK_SCHEMES
        safe_path = _redact_url_userinfo(cfg.path)

        logger.info(
            "parquet_source_fetch_start",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            scheme=scheme or "local",
        )

        if is_network:
            raise DataSourceError(
                f"Network-scheme Parquet fetch is not yet wired for '{safe_path}'."
            )

        if cfg.sha256 is not None:
            try:
                with fsspec.open(cfg.path, "rb") as f:
                    raw_bytes = f.read()
            except FileNotFoundError as exc:
                raise DataSourceError(f"Parquet file not found: {safe_path}") from exc
            except PermissionError as exc:
                raise DataSourceError(
                    f"Permission denied reading Parquet file: {safe_path}"
                ) from exc
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read Parquet from '{safe_path}': {exc}"
                ) from exc
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True
            try:
                df: pd.DataFrame = pd.read_parquet(BytesIO(raw_bytes))
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to parse Parquet from '{safe_path}': {exc}"
                ) from exc
            bytes_count = len(raw_bytes)
        else:
            sha256_verified = False
            try:
                df = pd.read_parquet(cfg.path)
            except FileNotFoundError as exc:
                raise DataSourceError(f"Parquet file not found: {safe_path}") from exc
            except PermissionError as exc:
                raise DataSourceError(
                    f"Permission denied reading Parquet file: {safe_path}"
                ) from exc
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read Parquet from '{safe_path}': {exc}"
                ) from exc
            bytes_count = -1  # not measured on the streaming path

        logger.info(
            "parquet_source_fetch_done",
            recipe=ctx.recipe_name,
            run_id=ctx.run_id,
            path=safe_path,
            rows=len(df),
            bytes=bytes_count,
            sha256_verified=sha256_verified,
            columns=list(df.columns),
        )
        return df


def _probe_fsspec_path(path: str, *, kind: str) -> None:
    """Confirm *path* exists on its fsspec-resolved filesystem.

    Used by file-backed sources' ``probe()`` so ``recotem validate`` catches
    missing or unreachable inputs (local paths, ``s3://``, ``gs://``, ``az://``)
    without loading any data.  Object-store backends require the same auth /
    network configuration to ``exists`` as to ``read``, so a successful exists
    check is a meaningful connectivity probe.
    """
    try:
        import fsspec
    except ImportError as exc:
        raise DataSourceError(
            "fsspec is required for path probing. Install it with: pip install recotem"
        ) from exc

    try:
        fs, resolved = fsspec.core.url_to_fs(path)
    except Exception as exc:
        raise DataSourceError(
            f"{kind} path {path!r} could not be resolved: {exc}"
        ) from exc

    try:
        if not fs.exists(resolved):
            raise DataSourceError(f"{kind} file not found: {path}")
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Failed to probe {kind} path {path!r}: {exc}") from exc
