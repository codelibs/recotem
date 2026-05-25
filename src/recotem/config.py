"""ServeConfig and TrainConfig — loaded from environment variables.

Deliberately avoids pydantic-settings to keep the dependency footprint small.
All parsing is done via plain dataclasses with a ``from_env()`` classmethod.

Environment variables:
  RECOTEM_API_KEYS          CSV of "<kid>:sha256:<hex64>" entries
  RECOTEM_HOST              Bind host (default 127.0.0.1 if no API keys)
  RECOTEM_PORT              Bind port (default 8080)
  RECOTEM_WATCH_INTERVAL    Poll interval in seconds (default 5; clamped 1–30)
  RECOTEM_LOG_FORMAT        "json" | "console" | "auto"
  RECOTEM_SIGNING_KEYS      CSV of "<kid>:<hex64>" entries for artifact signing
                              (64 hex chars = 32 raw bytes)
  RECOTEM_MAX_ARTIFACT_BYTES Max artifact size in bytes (default 2 GiB)
  RECOTEM_MAX_PAYLOAD_BYTES  Per-payload cap (post-HMAC-verify) for serve-side
                              deserialization. Smaller than max_artifact_bytes
                              to bound deserialization memory expansion.
                              (default 512 MiB; clamped 1 MiB–16 GiB)
  RECOTEM_ALLOWED_ORIGINS   CSV of allowed CORS origins (default empty = deny)
  RECOTEM_ALLOWED_HOSTS     CSV of allowed Host header values (default
                              "127.0.0.1,localhost")
  RECOTEM_ENV               Deployment environment identifier
  RECOTEM_DRAIN_SECONDS     Graceful drain on SIGTERM (default 30)
  RECOTEM_METADATA_FIELD_DENY  CSV of metadata fields to strip post-join
  RECOTEM_MAX_DOWNLOAD_BYTES   Max bytes for HTTP/HTTPS datasource fetch
                                 (default 256 MiB; clamped 1 MiB–16 GiB)
  RECOTEM_HTTP_TIMEOUT_SECONDS Timeout in seconds for HTTP/HTTPS datasource
                                 fetch (default 30; clamped 1–600)
  RECOTEM_STARTUP_PARALLELISM  Number of parallel threads used to load
                                 artifacts at startup (default min(recipes, 8);
                                 clamped 1–32)
  RECOTEM_MAX_SQL_ROWS         Hard cap on rows returned by the SQL source
                                 (default 50_000_000; clamped [1_000, 500_000_000])
  RECOTEM_SQL_ALLOW_PRIVATE    Truthy (1/true/yes/on) opts the SQL source into
                                 accepting private/loopback host addresses.
                                 Default refuses RFC1918 / 127.0.0.0/8 to
                                 block SSRF via crafted DSNs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import structlog

# ---------------------------------------------------------------------------
# ConfigError — typed exception for configuration failures
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised for operator-visible configuration failures (exit code 8).

    Covers: missing RECOTEM_SIGNING_KEYS, malformed RECOTEM_API_KEYS (duplicate
    kid, bad format), invalid RECOTEM_PORT, and insecure-flag misuse outside
    permitted environments.

    This exception type is intentionally kept at the top-level config module so
    both the training and serving sub-packages can import it without a circular
    dependency (training/ and serving/ never import each other).
    """


_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080
_DEFAULT_WATCH_INTERVAL = 5
_DEFAULT_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
_MIN_ARTIFACT_BYTES = 1 * 1024 * 1024  # 1 MiB
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024  # 16 GiB
_DEFAULT_MAX_PAYLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB
_MIN_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MiB
_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024 * 1024  # 16 GiB
_DEFAULT_DRAIN_SECONDS = 30
_DEFAULT_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Startup parallelism for artifact loading (per-recipe threads at serve startup)
_MIN_STARTUP_PARALLELISM = 1
_MAX_STARTUP_PARALLELISM = 32
# Sentinel: 0 means "derive from len(recipes) at startup, capped at 8"
_DEFAULT_STARTUP_PARALLELISM_SENTINEL = 0

# Exact hex length for a sha256 hash: 64 hex chars = 32 bytes.
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Environments that permit --insecure-no-auth
_INSECURE_ALLOWED_ENVS: frozenset[str] = frozenset({"development", "dev", "test"})


def _split_csv_env(name: str, default: list[str]) -> list[str]:
    """Return a CSV-split, stripped, empty-skipping env value, or *default*.

    Falls back to *default* when the env value is unset, empty, or contains
    only separators/whitespace (e.g. ``" , , "``).  Never returns an empty
    list when *default* is non-empty — handing ``[]`` to
    ``TrustedHostMiddleware`` would silently 400 every request.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    return parts if parts else list(default)


def _clamped_int_env(name: str, default: int, lo: int, hi: int) -> int:
    """Return an integer env value clamped to ``[lo, hi]``, falling back to *default*.

    On unset / empty / unparseable input, returns *default* unchanged.
    Emits a structured warning log when the value is clamped.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning(
            "env_var_unparseable",
            name=name,
            raw=raw,
            fallback=default,
        )
        return default
    clamped = max(lo, min(hi, value))
    if clamped != value:
        _logger.warning(
            "env_var_clamped",
            name=name,
            requested=value,
            clamped=clamped,
            lo=lo,
            hi=hi,
        )
    return clamped


# ---------------------------------------------------------------------------
# ApiKeyEntry — parsed "<kid>:sha256:<hex64>"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiKeyEntry:
    """A single API key entry parsed from RECOTEM_API_KEYS.

    Attributes
    ----------
    kid:
        Key identifier (arbitrary non-empty string).
    sha256_hex:
        64-char lowercase hex digest produced by
        ``recotem.serving.auth._hash_api_key`` (scrypt KDF with the
        ``recotem.api-key.v1`` salt) — NOT a plain SHA-256.  The
        ``sha256:`` literal in the wire format is a digest-family prefix,
        not the algorithm name.  The field is named ``sha256_hex`` for
        backward compatibility with existing config; the value itself is
        a scrypt digest.
    """

    kid: str
    sha256_hex: str

    @classmethod
    def parse(cls, raw: str) -> ApiKeyEntry:
        """Parse a single ``<kid>:sha256:<hex64>`` string.

        Raises
        ------
        ValueError
            If the entry is malformed.
        """
        raw = raw.strip()
        # Expected format: "<kid>:sha256:<hex64>"
        parts = raw.split(":", 2)
        if len(parts) != 3 or parts[1].lower() != "sha256":
            raise ValueError(
                f"malformed API key entry {raw!r}: expected '<kid>:sha256:<hex64>'"
            )
        kid, _, hex_hash = parts[0], parts[1], parts[2]
        if not kid:
            raise ValueError("malformed API key entry: kid must not be empty")
        if not _SHA256_HEX_RE.match(hex_hash):
            raise ValueError(
                f"malformed API key entry for kid {kid!r}: "
                f"hash must be exactly 64 hex chars, got {len(hex_hash)}"
            )
        return cls(kid=kid, sha256_hex=hex_hash.lower())


# ---------------------------------------------------------------------------
# ServeConfig
# ---------------------------------------------------------------------------


@dataclass
class ServeConfig:
    """Configuration for ``recotem serve``, loaded from environment variables.

    Do not construct directly in production code — use ``from_env()``.
    """

    # Auth
    api_keys: list[ApiKeyEntry] = field(default_factory=list)

    # Network
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT

    # Watcher
    watch_interval: float = float(_DEFAULT_WATCH_INTERVAL)

    # Logging
    log_format: str = "auto"

    # Signing
    signing_keys_raw: str = ""  # raw env value; KeyRing built in create_app

    # Artifact caps
    max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES
    # Per-payload cap (post-HMAC-verify) for serve-side deserialization.
    # Smaller than max_artifact_bytes to bound deserialization memory expansion.
    max_payload_bytes: int = _DEFAULT_MAX_PAYLOAD_BYTES

    # CORS / TrustedHost
    allowed_origins: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(
        default_factory=lambda: list(_DEFAULT_ALLOWED_HOSTS)
    )

    # Environment tag
    env: str = ""

    # Graceful drain
    drain_seconds: int = _DEFAULT_DRAIN_SECONDS

    # Metadata field deny-list (post-join column drop)
    metadata_field_deny: list[str] = field(default_factory=list)

    # Unsafe mode flags (set by CLI, not env)
    insecure_no_auth: bool = False
    dev_allow_unsigned: bool = False

    # Recipes directory — injected by the CLI before calling create_app().
    # Not sourced from an env var; must be set explicitly.
    recipes_dir: str = ""

    # Startup parallelism — number of threads used to load artifacts in parallel
    # at startup.  0 = sentinel meaning "min(len(recipes), 8)" (resolved in
    # create_app).  Set via RECOTEM_STARTUP_PARALLELISM (clamped [1, 32]).
    startup_parallelism: int = _DEFAULT_STARTUP_PARALLELISM_SENTINEL

    @classmethod
    def from_env(cls) -> ServeConfig:
        """Build a :class:`ServeConfig` from the current environment.

        Raises
        ------
        ConfigError
            If any env var has an invalid value (malformed API key entry,
            duplicate API key kid, out-of-range port,
            RECOTEM_MAX_PAYLOAD_BYTES > RECOTEM_MAX_ARTIFACT_BYTES, etc.).
            The error message never includes key plaintext.
        """
        cfg = cls()

        # RECOTEM_API_KEYS
        raw_keys = os.environ.get("RECOTEM_API_KEYS", "").strip()
        if raw_keys:
            entries: list[ApiKeyEntry] = []
            for raw_entry in raw_keys.split(","):
                raw_entry = raw_entry.strip()
                if not raw_entry:
                    continue
                try:
                    entries.append(ApiKeyEntry.parse(raw_entry))
                except ValueError as exc:
                    raise ConfigError(
                        f"RECOTEM_API_KEYS contains an invalid entry: {exc}"
                    ) from exc
            # Detect duplicate kids — the first kid wins in KeyRing but the
            # config is almost certainly wrong, so fail fast.
            seen_kids: set[str] = set()
            for entry in entries:
                if entry.kid in seen_kids:
                    raise ConfigError(
                        f"RECOTEM_API_KEYS contains duplicate kid {entry.kid!r}; "
                        "each kid must appear at most once"
                    )
                seen_kids.add(entry.kid)
            cfg.api_keys = entries

        # RECOTEM_HOST
        host_env = os.environ.get("RECOTEM_HOST", "").strip()
        if host_env:
            cfg.host = host_env

        # RECOTEM_PORT
        port_env = os.environ.get("RECOTEM_PORT", "").strip()
        if port_env:
            try:
                port_val = int(port_env)
            except ValueError as exc:
                raise ConfigError(
                    f"RECOTEM_PORT must be an integer, got {port_env!r}: {exc}"
                ) from exc
            if not (1 <= port_val <= 65535):
                raise ConfigError(
                    f"RECOTEM_PORT must be in range 1–65535, got {port_val}"
                )
            cfg.port = port_val

        # RECOTEM_WATCH_INTERVAL (clamp 1–30)
        interval_env = os.environ.get("RECOTEM_WATCH_INTERVAL", "").strip()
        if interval_env:
            try:
                raw_interval = float(interval_env)
            except ValueError as exc:
                raise ConfigError(
                    f"RECOTEM_WATCH_INTERVAL must be a number, "
                    f"got {interval_env!r}: {exc}"
                ) from exc
            cfg.watch_interval = max(1.0, min(30.0, raw_interval))

        # RECOTEM_LOG_FORMAT
        _VALID_LOG_FORMATS = frozenset({"auto", "json", "console"})
        fmt_env = os.environ.get("RECOTEM_LOG_FORMAT", "").strip().lower()
        if fmt_env:
            if fmt_env not in _VALID_LOG_FORMATS:
                raise ConfigError(
                    f"RECOTEM_LOG_FORMAT must be one of {sorted(_VALID_LOG_FORMATS)}, "
                    f"got {fmt_env!r}"
                )
            cfg.log_format = fmt_env

        # RECOTEM_SIGNING_KEYS (raw; KeyRing instantiation happens elsewhere)
        cfg.signing_keys_raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()

        # RECOTEM_MAX_ARTIFACT_BYTES (clamped to [1 MiB, 16 GiB])
        cfg.max_artifact_bytes = _clamped_int_env(
            "RECOTEM_MAX_ARTIFACT_BYTES",
            _DEFAULT_MAX_ARTIFACT_BYTES,
            _MIN_ARTIFACT_BYTES,
            _MAX_ARTIFACT_BYTES,
        )

        # RECOTEM_MAX_PAYLOAD_BYTES (clamped to [1 MiB, 16 GiB])
        # Per-payload cap (post-HMAC-verify) for serve-side deserialization.
        # Smaller than max_artifact_bytes to bound deserialization memory expansion.
        cfg.max_payload_bytes = _clamped_int_env(
            "RECOTEM_MAX_PAYLOAD_BYTES",
            _DEFAULT_MAX_PAYLOAD_BYTES,
            _MIN_PAYLOAD_BYTES,
            _MAX_PAYLOAD_BYTES,
        )

        cfg.allowed_origins = _split_csv_env("RECOTEM_ALLOWED_ORIGINS", [])
        cfg.allowed_hosts = _split_csv_env(
            "RECOTEM_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS
        )

        # RECOTEM_ENV
        cfg.env = os.environ.get("RECOTEM_ENV", "").strip()

        # RECOTEM_DRAIN_SECONDS (clamped 1–300; warns if clamped)
        cfg.drain_seconds = _clamped_int_env(
            "RECOTEM_DRAIN_SECONDS", _DEFAULT_DRAIN_SECONDS, lo=1, hi=300
        )

        cfg.metadata_field_deny = _split_csv_env("RECOTEM_METADATA_FIELD_DENY", [])

        # RECOTEM_STARTUP_PARALLELISM (clamped 1–32; 0 = derive from recipe count)
        raw_parallelism = os.environ.get("RECOTEM_STARTUP_PARALLELISM", "").strip()
        if raw_parallelism:
            cfg.startup_parallelism = _clamped_int_env(
                "RECOTEM_STARTUP_PARALLELISM",
                _DEFAULT_STARTUP_PARALLELISM_SENTINEL,
                _MIN_STARTUP_PARALLELISM,
                _MAX_STARTUP_PARALLELISM,
            )
        # else leave as sentinel 0 → resolved at startup in create_app

        # Invariant: payload cap must not exceed the artifact cap.
        # RECOTEM_MAX_PAYLOAD_BYTES is documented as "Smaller than
        # RECOTEM_MAX_ARTIFACT_BYTES to bound deserialization memory expansion."
        if cfg.max_payload_bytes > cfg.max_artifact_bytes:
            raise ConfigError(
                f"RECOTEM_MAX_PAYLOAD_BYTES ({cfg.max_payload_bytes}) must be "
                f"<= RECOTEM_MAX_ARTIFACT_BYTES ({cfg.max_artifact_bytes}); "
                "reduce RECOTEM_MAX_PAYLOAD_BYTES or increase RECOTEM_MAX_ARTIFACT_BYTES"
            )

        return cfg

    def apply_auth_posture(self) -> None:
        """Enforce security posture rules for the host binding.

        If no API keys are configured and ``insecure_no_auth`` is False,
        force HOST to ``127.0.0.1`` regardless of ``RECOTEM_HOST``.

        Must be called after both ``from_env()`` and CLI flag injection.
        """
        if not self.api_keys and not self.insecure_no_auth:
            if self.host != "127.0.0.1":
                _logger.warning(
                    "host_forced_to_loopback",
                    requested_host=self.host,
                    reason="no_api_keys_and_no_insecure_flag",
                    hint=(
                        "Set RECOTEM_API_KEYS or pass --insecure-no-auth "
                        "(development only)."
                    ),
                )
            self.host = "127.0.0.1"

    def validate_insecure_flags(self) -> None:
        """Validate that unsafe CLI flags are only used in safe environments.

        Raises
        ------
        ConfigError
            If ``--insecure-no-auth`` or ``--dev-allow-unsigned`` is used in
            a non-development environment.
        """
        if self.insecure_no_auth and self.env.lower() not in _INSECURE_ALLOWED_ENVS:
            raise ConfigError(
                "--insecure-no-auth is only permitted when RECOTEM_ENV is "
                f"one of {sorted(_INSECURE_ALLOWED_ENVS)}. "
                f"Current RECOTEM_ENV={self.env!r}."
            )

        if self.dev_allow_unsigned and self.env.lower() != "development":
            raise ConfigError(
                "--dev-allow-unsigned requires RECOTEM_ENV=development. "
                f"Current RECOTEM_ENV={self.env!r}."
            )


# ---------------------------------------------------------------------------
# TrainConfig
# ---------------------------------------------------------------------------


@dataclass
class TrainConfig:
    """Runtime configuration for ``recotem train``, loaded from environment.

    Most training configuration lives in the Recipe YAML.  TrainConfig covers
    operator-level overrides that are not part of the recipe schema.
    """

    # Signing keys for artifact HMAC (raw env value).
    signing_keys_raw: str = ""

    # Artifact root containment (forwarded to loader).
    artifact_root: str = ""

    # Log format.
    log_format: str = "auto"

    @classmethod
    def from_env(cls) -> TrainConfig:
        """Build a :class:`TrainConfig` from the current environment."""
        cfg = cls()
        cfg.signing_keys_raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()
        cfg.artifact_root = os.environ.get("RECOTEM_ARTIFACT_ROOT", "").strip()
        cfg.log_format = os.environ.get("RECOTEM_LOG_FORMAT", "auto").strip().lower()
        return cfg


# ---------------------------------------------------------------------------
# Network-fetch caps (used by datasource/csv.py for HTTP/HTTPS sources)
# ---------------------------------------------------------------------------

DEFAULT_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB
_MIN_DOWNLOAD_BYTES = 1 * 1024 * 1024  # 1 MiB
_MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024 * 1024  # 16 GiB

DEFAULT_HTTP_TIMEOUT_SECONDS = 30
_MIN_HTTP_TIMEOUT_SECONDS = 1
_MAX_HTTP_TIMEOUT_SECONDS = 600


def get_max_download_bytes() -> int:
    """Return RECOTEM_MAX_DOWNLOAD_BYTES, clamped to [1 MiB, 16 GiB]."""
    return _clamped_int_env(
        "RECOTEM_MAX_DOWNLOAD_BYTES",
        DEFAULT_MAX_DOWNLOAD_BYTES,
        _MIN_DOWNLOAD_BYTES,
        _MAX_DOWNLOAD_BYTES,
    )


def get_http_timeout_seconds() -> int:
    """Return RECOTEM_HTTP_TIMEOUT_SECONDS, clamped to [1, 600]."""
    return _clamped_int_env(
        "RECOTEM_HTTP_TIMEOUT_SECONDS",
        DEFAULT_HTTP_TIMEOUT_SECONDS,
        _MIN_HTTP_TIMEOUT_SECONDS,
        _MAX_HTTP_TIMEOUT_SECONDS,
    )


_TRUTHY_ENV_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def is_truthy_env(value: str | None) -> bool:
    """Return True iff *value* is a truthy env-var string.

    Accepts the same set of truthy values recognised across all Recotem env
    variables: ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive).
    ``None`` and the empty string are treated as falsy.
    """
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_ENV_VALUES


def get_http_allow_private() -> bool:
    """Return True if HTTP fetches to private/loopback/link-local IPs are allowed.

    Defaults to False (secure-by-default).  Set ``RECOTEM_HTTP_ALLOW_PRIVATE``
    to ``1`` / ``true`` / ``yes`` / ``on`` to allow recipes whose
    ``source.path`` resolves to RFC1918 / loopback / link-local / reserved
    addresses.  Operators with internal HTTP origins (lab CI, intranet
    mirrors) opt in explicitly; production deployments leave it off so a
    malicious recipe cannot hit cloud-metadata services or sibling pods.
    """
    return is_truthy_env(os.environ.get("RECOTEM_HTTP_ALLOW_PRIVATE"))


def get_lock_dir() -> str:
    """Return ``RECOTEM_LOCK_DIR`` (host-local lock dir for remote outputs).

    Empty string falls back to ``<tempdir>/recotem-locks/`` at the call site.
    Centralised here so every ``RECOTEM_*`` variable is enumerable via
    :mod:`recotem.config`.
    """
    return os.environ.get("RECOTEM_LOCK_DIR", "").strip()


# ---------------------------------------------------------------------------
# SQL row cap (used by datasource/sql.py)
# ---------------------------------------------------------------------------

_SQL_ROW_CAP_MIN = 1_000
_SQL_ROW_CAP_MAX = 500_000_000
_SQL_ROW_CAP_DEFAULT = 50_000_000


def get_max_sql_rows() -> int:
    """Return RECOTEM_MAX_SQL_ROWS, clamped to [1 000, 500 000 000]."""
    return _clamped_int_env(
        "RECOTEM_MAX_SQL_ROWS",
        _SQL_ROW_CAP_DEFAULT,
        _SQL_ROW_CAP_MIN,
        _SQL_ROW_CAP_MAX,
    )


def sql_allow_private() -> bool:
    """Return True if SQL sources may connect to private/loopback host addresses.

    Defaults to False (secure-by-default).  Set ``RECOTEM_SQL_ALLOW_PRIVATE``
    to ``1`` / ``true`` / ``yes`` / ``on`` to allow recipes whose SQL data
    source host resolves to RFC1918 / loopback / link-local / reserved
    addresses.
    """
    return is_truthy_env(os.environ.get("RECOTEM_SQL_ALLOW_PRIVATE"))
