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
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080
_DEFAULT_WATCH_INTERVAL = 5
_DEFAULT_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
_DEFAULT_DRAIN_SECONDS = 30
_DEFAULT_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

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
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


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

    @classmethod
    def from_env(cls) -> ServeConfig:
        """Build a :class:`ServeConfig` from the current environment.

        Raises
        ------
        ValueError
            If any env var has an invalid value (malformed API key entry,
            out-of-range port, etc.).  The error message never includes the
            key plaintext.
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
                    raise ValueError(
                        f"RECOTEM_API_KEYS contains an invalid entry: {exc}"
                    ) from exc
            cfg.api_keys = entries

        # RECOTEM_HOST
        host_env = os.environ.get("RECOTEM_HOST", "").strip()
        if host_env:
            cfg.host = host_env

        # RECOTEM_PORT
        port_env = os.environ.get("RECOTEM_PORT", "").strip()
        if port_env:
            try:
                cfg.port = int(port_env)
            except ValueError as exc:
                raise ValueError(
                    f"RECOTEM_PORT must be an integer, got {port_env!r}: {exc}"
                ) from exc

        # RECOTEM_WATCH_INTERVAL (clamp 1–30)
        interval_env = os.environ.get("RECOTEM_WATCH_INTERVAL", "").strip()
        if interval_env:
            try:
                raw_interval = float(interval_env)
            except ValueError as exc:
                raise ValueError(
                    f"RECOTEM_WATCH_INTERVAL must be a number, "
                    f"got {interval_env!r}: {exc}"
                ) from exc
            cfg.watch_interval = max(1.0, min(30.0, raw_interval))

        # RECOTEM_LOG_FORMAT
        fmt_env = os.environ.get("RECOTEM_LOG_FORMAT", "").strip().lower()
        if fmt_env in ("json", "console", "auto"):
            cfg.log_format = fmt_env

        # RECOTEM_SIGNING_KEYS (raw; KeyRing instantiation happens elsewhere)
        cfg.signing_keys_raw = os.environ.get("RECOTEM_SIGNING_KEYS", "").strip()

        # RECOTEM_MAX_ARTIFACT_BYTES
        max_bytes_env = os.environ.get("RECOTEM_MAX_ARTIFACT_BYTES", "").strip()
        if max_bytes_env:
            try:
                cfg.max_artifact_bytes = int(max_bytes_env)
            except ValueError as exc:
                raise ValueError(
                    f"RECOTEM_MAX_ARTIFACT_BYTES must be an integer, "
                    f"got {max_bytes_env!r}: {exc}"
                ) from exc

        cfg.allowed_origins = _split_csv_env("RECOTEM_ALLOWED_ORIGINS", [])
        cfg.allowed_hosts = _split_csv_env(
            "RECOTEM_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS
        )

        # RECOTEM_ENV
        cfg.env = os.environ.get("RECOTEM_ENV", "").strip()

        # RECOTEM_DRAIN_SECONDS
        drain_env = os.environ.get("RECOTEM_DRAIN_SECONDS", "").strip()
        if drain_env:
            try:
                cfg.drain_seconds = int(drain_env)
            except ValueError as exc:
                raise ValueError(
                    f"RECOTEM_DRAIN_SECONDS must be an integer, "
                    f"got {drain_env!r}: {exc}"
                ) from exc

        cfg.metadata_field_deny = _split_csv_env("RECOTEM_METADATA_FIELD_DENY", [])

        return cfg

    def apply_auth_posture(self) -> None:
        """Enforce security posture rules for the host binding.

        If no API keys are configured and ``insecure_no_auth`` is False,
        force HOST to ``127.0.0.1`` regardless of ``RECOTEM_HOST``.

        Must be called after both ``from_env()`` and CLI flag injection.
        """
        if not self.api_keys and not self.insecure_no_auth:
            self.host = "127.0.0.1"

    def validate_insecure_flags(self) -> None:
        """Validate that unsafe CLI flags are only used in safe environments.

        Raises
        ------
        ValueError
            If ``--insecure-no-auth`` or ``--dev-allow-unsigned`` is used in
            a non-development environment.
        """
        if self.insecure_no_auth and self.env.lower() not in _INSECURE_ALLOWED_ENVS:
            raise ValueError(
                "--insecure-no-auth is only permitted when RECOTEM_ENV is "
                f"one of {sorted(_INSECURE_ALLOWED_ENVS)}. "
                f"Current RECOTEM_ENV={self.env!r}."
            )

        if self.dev_allow_unsigned and self.env.lower() != "development":
            raise ValueError(
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


def get_http_allow_private() -> bool:
    """Return True if HTTP fetches to private/loopback/link-local IPs are allowed.

    Defaults to False (secure-by-default).  Set ``RECOTEM_HTTP_ALLOW_PRIVATE``
    to ``1`` / ``true`` / ``yes`` / ``on`` to allow recipes whose
    ``source.path`` resolves to RFC1918 / loopback / link-local / reserved
    addresses.  Operators with internal HTTP origins (lab CI, intranet
    mirrors) opt in explicitly; production deployments leave it off so a
    malicious recipe cannot hit cloud-metadata services or sibling pods.
    """
    raw = os.environ.get("RECOTEM_HTTP_ALLOW_PRIVATE", "").strip().lower()
    return raw in _TRUTHY_ENV_VALUES
