"""CSVSource and ParquetSource — fsspec-backed via pandas."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, ClassVar, Literal
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field

from recotem._http_fetch import (
    NETWORK_SCHEMES as _NETWORK_SCHEMES,
)
from recotem._http_fetch import (
    HttpFetchError,
    assert_host_public,
    fetch_http_bytes,
    infer_compression,
    redact_url_userinfo,
    verify_sha256,
)
from recotem._size_cap import SizeCapExceededError, SizeCapProbeError, check_size_cap
from recotem.config import (
    get_http_allow_private,
    get_http_timeout_seconds,
    get_max_download_bytes,
)
from recotem.datasource.base import DataSourceError, FetchContext

if TYPE_CHECKING:
    import pandas as pd

logger = structlog.get_logger(__name__)

_redact_url_userinfo = redact_url_userinfo
_infer_compression = infer_compression


def _verify_sha256(actual: bytes, expected_hex: str) -> None:
    """sha256 verification that raises :class:`DataSourceError` on mismatch."""
    try:
        verify_sha256(actual, expected_hex)
    except HttpFetchError as exc:
        raise DataSourceError(str(exc)) from exc


def _get_max_download_bytes() -> int:
    """Indirection so tests can monkeypatch a smaller cap."""
    return get_max_download_bytes()


def _check_size_cap(path: str, safe_path: str, kind: str) -> None:
    """Enforce RECOTEM_MAX_DOWNLOAD_BYTES on local and object-store paths.

    Delegates to :func:`recotem._size_cap.check_size_cap` which handles all
    path schemes including ``file://localhost/…`` URIs correctly.

    Raises
    ------
    DataSourceError
        If the file size exceeds the cap.  The message names
        ``RECOTEM_MAX_DOWNLOAD_BYTES`` so operators know which env var to
        raise if they legitimately need larger inputs.
    """
    try:
        check_size_cap(path, cap=_get_max_download_bytes(), label=kind)
    except SizeCapExceededError as exc:
        raise DataSourceError(str(exc)) from exc
    except SizeCapProbeError as exc:
        raise DataSourceError(f"Size probe for {kind} source failed: {exc}") from exc


def _validate_required_columns(
    df: pd.DataFrame,
    ctx: FetchContext,
    safe_path: str,
) -> None:
    """Validate that required interaction columns exist in *df*.

    The caller (pipeline or test) populates ctx.extra with the required
    column names under the keys user_column, item_column, and
    optionally time_column.  When these keys are absent from ctx.extra
    (e.g. a plugin test that does not pass schema context), the check is skipped.

    Raises
    ------
    DataSourceError
        If a required column is absent from *df*.  The message names the
        missing column and lists up to 10 available column names so operators
        can diagnose typos in the recipe schema without needing to load the
        file manually.
    """
    user_col: str | None = ctx.extra.get("user_column")  # type: ignore[assignment]
    item_col: str | None = ctx.extra.get("item_column")  # type: ignore[assignment]
    time_col: str | None = ctx.extra.get("time_column")  # type: ignore[assignment]

    cols_to_check: list[str] = []
    if user_col:
        cols_to_check.append(user_col)
    if item_col:
        cols_to_check.append(item_col)
    if time_col:
        cols_to_check.append(time_col)

    if not cols_to_check:
        return  # no schema context — skip

    df_columns = set(df.columns)
    for col in cols_to_check:
        if col not in df_columns:
            available = sorted(str(c) for c in df.columns)[:10]
            raise DataSourceError(
                f"required column {col!r} not found in source {safe_path!r}; "
                f"available columns: {available}"
            )


def _fetch_http_bytes(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    recipe_name: str,
    run_id: str,
) -> bytes:
    """Wrap :func:`recotem._http_fetch.fetch_http_bytes` with DataSourceError."""
    try:
        return fetch_http_bytes(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            log_event="csv_source",
            log_context={"recipe": recipe_name, "run_id": run_id},
        )
    except HttpFetchError as exc:
        raise DataSourceError(str(exc)) from exc


class CSVConfig(BaseModel, extra="forbid"):
    """Configuration schema for CSV sources."""

    # ``Literal`` (not ``str`` + pattern) is required for the discriminated-
    # union JSON Schema emitted by ``recotem schema``: pydantic refuses to
    # discriminate on a non-Literal field.
    type: Literal["csv"] = "csv"
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
    no_expand_fields: ClassVar[frozenset[str]] = frozenset()

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
            raw_bytes = _fetch_http_bytes(
                cfg.path,
                timeout=get_http_timeout_seconds(),
                max_bytes=_get_max_download_bytes(),
                recipe_name=ctx.recipe_name,
                run_id=ctx.run_id,
            )
            # sha256 is guaranteed present by the recipe loader's
            # _enforce_sha256_for_network_paths post-validator. Verify here.
            assert cfg.sha256 is not None  # noqa: S101 — loader invariant
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
                df = pd.read_csv(BytesIO(raw_bytes), **read_kwargs)
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to parse CSV from '{safe_path}': {exc}"
                ) from exc
            if df.empty:
                raise DataSourceError(
                    f"CSV file '{safe_path}' is empty (no data rows after header)."
                )
            _validate_required_columns(df, ctx, safe_path)
            logger.info(
                "csv_source_fetch_done",
                recipe=ctx.recipe_name,
                run_id=ctx.run_id,
                path=safe_path,
                rows=len(df),
                bytes=len(raw_bytes),
                sha256_verified=sha256_verified,
                columns=list(df.columns),
            )
            return df

        # Non-network path — enforce the source-body byte cap before reading.
        _check_size_cap(cfg.path, safe_path, "CSV")

        if cfg.sha256 is not None:
            cap = _get_max_download_bytes()
            try:
                with fsspec.open(cfg.path, "rb") as f:
                    raw_bytes = f.read(cap + 1)
            except FileNotFoundError as exc:
                raise DataSourceError(f"CSV file not found: {safe_path}") from exc
            except PermissionError as exc:
                raise DataSourceError(
                    f"Permission denied reading CSV file: {safe_path}"
                ) from exc
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read CSV from '{safe_path}': {exc}"
                ) from exc
            if len(raw_bytes) > cap:
                raise DataSourceError(
                    f"CSV file '{safe_path}' exceeds RECOTEM_MAX_DOWNLOAD_BYTES ({cap}) — "
                    "increase the cap or split the file."
                )
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
            except (MemoryError, RecursionError):
                raise
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
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read CSV from '{safe_path}': {exc}"
                ) from exc
            bytes_count = -1  # not measured on the streaming path

        if df.empty:
            raise DataSourceError(
                f"CSV file '{safe_path}' is empty (no data rows after header)."
            )

        _validate_required_columns(df, ctx, safe_path)
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

    type: Literal["parquet"] = "parquet"
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
    no_expand_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: ParquetConfig) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError as exc:
            raise DataSourceError(
                "pandas is required for ParquetSource. "
                "Install it with: pip install recotem"
            ) from exc
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise DataSourceError(
                "pyarrow is required for ParquetSource. "
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
            raw_bytes = _fetch_http_bytes(
                cfg.path,
                timeout=get_http_timeout_seconds(),
                max_bytes=_get_max_download_bytes(),
                recipe_name=ctx.recipe_name,
                run_id=ctx.run_id,
            )
            assert cfg.sha256 is not None  # noqa: S101 — loader invariant
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True
            try:
                df = pd.read_parquet(BytesIO(raw_bytes))
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to parse Parquet from '{safe_path}': {exc}"
                ) from exc
            logger.info(
                "parquet_source_fetch_done",
                recipe=ctx.recipe_name,
                run_id=ctx.run_id,
                path=safe_path,
                rows=len(df),
                bytes=len(raw_bytes),
                sha256_verified=sha256_verified,
                columns=list(df.columns),
            )
            return df

        # Non-network path — enforce the source-body byte cap before reading.
        _check_size_cap(cfg.path, safe_path, "Parquet")

        if cfg.sha256 is not None:
            cap = _get_max_download_bytes()
            try:
                with fsspec.open(cfg.path, "rb") as f:
                    raw_bytes = f.read(cap + 1)
            except FileNotFoundError as exc:
                raise DataSourceError(f"Parquet file not found: {safe_path}") from exc
            except PermissionError as exc:
                raise DataSourceError(
                    f"Permission denied reading Parquet file: {safe_path}"
                ) from exc
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                raise DataSourceError(
                    f"Failed to read Parquet from '{safe_path}': {exc}"
                ) from exc
            if len(raw_bytes) > cap:
                raise DataSourceError(
                    f"Parquet file '{safe_path}' exceeds RECOTEM_MAX_DOWNLOAD_BYTES ({cap}) — "
                    "increase the cap or split the file."
                )
            _verify_sha256(raw_bytes, cfg.sha256)
            sha256_verified = True
            try:
                df: pd.DataFrame = pd.read_parquet(BytesIO(raw_bytes))
            except (MemoryError, RecursionError):
                raise
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
            except (MemoryError, RecursionError):
                raise
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

    For ``http://`` / ``https://`` paths, calling ``fsspec.exists()`` would
    bypass the SSRF guard, byte cap, sha256 check, and redirect-scheme policy
    implemented in :func:`recotem._http_fetch.fetch_http_bytes`.  Instead,
    only the host-public assertion is run here; the remaining controls fire at
    actual fetch time.
    """
    scheme = urlparse(path).scheme.lower()
    if scheme in _NETWORK_SCHEMES:
        try:
            assert_host_public(path, allow_private=get_http_allow_private())
        except HttpFetchError as exc:
            raise DataSourceError(f"{kind} HTTP probe refused: {exc}") from exc
        # Full byte-content validation (sha256, byte cap, redirect policy) runs
        # at fetch time. Skipping fsspec.exists() here keeps `recotem validate`
        # from bypassing _http_fetch's controls (CVE-class SSRF guard).
        return

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
