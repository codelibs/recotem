"""HMAC-SHA256 signing, verification, and safe deserialization for Recotem artifacts.

Security posture (from spec Section 8)
---------------------------------------
Pickle is the irspack-native serialization format and cannot be avoided for
scipy sparse matrices and numpy arrays.  The risk is mitigated by four
independent, layered controls:

1. Strong magic / version / size checks before any deserialization (format.py).
2. HMAC-SHA256 signature verification with multi-kid support and constant-time
   compare via ``hmac.compare_digest``; signing keys are never logged (only
   the kid is surfaced in log events).
3. Hand-enumerated FQCN allow-list in ``SafeUnpickler.find_class`` -- an RCE
   backstop that is independent of the HMAC.  Module-prefix wildcards are
   explicitly rejected; every (module, name) pair must appear verbatim in
   ``_ALLOWED_CLASSES``.
4. Required signing key for both train and serve; a misconfigured deployment
   fails closed rather than loading arbitrary files.

Key rotation
------------
``RECOTEM_SIGNING_KEYS`` is a comma-separated list of ``<kid>:<hex32>``
entries.  ``recotem train`` uses ``KeyRing.active_kid`` (the first entry).
``recotem serve`` verifies against any entry.  Adding a new key, retraining,
then removing the old key is a zero-downtime rotation.  Each artifact's kid
is logged on load; the raw key bytes are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import pickle
import struct
from typing import Any

import structlog

from recotem.artifact.format import ArtifactError

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FQCN allow-list (hand-enumerated per spec Section 8)
# ---------------------------------------------------------------------------

_ALLOWED_CLASSES: frozenset[tuple[str, str]] = frozenset(
    {
        # Recotem compat wrapper
        ("recotem.serving._compat", "IDMappedRecommender"),
        # irspack id mapping
        ("irspack.utils.id_mapping", "IDMapper"),
        # irspack recommenders
        ("irspack.recommenders", "IALSRecommender"),
        ("irspack.recommenders", "CosineKNNRecommender"),
        ("irspack.recommenders", "TopPopRecommender"),
        ("irspack.recommenders", "RP3betaRecommender"),
        ("irspack.recommenders", "DenseSLIMRecommender"),
        ("irspack.recommenders", "TruncatedSVDRecommender"),
        ("irspack.recommenders", "BPRFMRecommender"),
        # numpy
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy.core.multiarray", "scalar"),
        # scipy sparse
        ("scipy.sparse._csr", "csr_matrix"),
        ("scipy.sparse._csc", "csc_matrix"),
        ("scipy.sparse._coo", "coo_matrix"),
        # builtins
        ("builtins", "int"),
        ("builtins", "float"),
        ("builtins", "bool"),
        ("builtins", "list"),
        ("builtins", "tuple"),
        ("builtins", "dict"),
        ("builtins", "str"),
        ("builtins", "bytes"),
        ("builtins", "complex"),
        ("builtins", "set"),
        ("builtins", "frozenset"),
        # collections
        ("collections", "OrderedDict"),
    }
)


# ---------------------------------------------------------------------------
# KeyRing
# ---------------------------------------------------------------------------


class KeyRing:
    """Immutable map from kid to 32-byte HMAC key.

    Construction
    ------------
    Pass one or more ``"<kid>:<hex32>"`` strings (the format used by
    ``RECOTEM_SIGNING_KEYS``).  Entries may be supplied as a single
    comma-separated string or as individual positional arguments.

    The first entry becomes ``active_kid`` (used by the trainer).

    Examples
    --------
    >>> kr = KeyRing("prod-2026:" + "aa" * 32)
    >>> kr.active_kid
    'prod-2026'
    """

    def __init__(self, *entries: str) -> None:
        if not entries:
            raise ArtifactError("KeyRing requires at least one key entry")

        # Accept a single comma-separated string as a convenience
        flat: list[str] = []
        for entry in entries:
            flat.extend(e.strip() for e in entry.split(",") if e.strip())

        if not flat:
            raise ArtifactError("KeyRing requires at least one key entry")

        self._keys: dict[str, bytes] = {}
        self._order: list[str] = []

        for entry in flat:
            if ":" not in entry:
                raise ArtifactError(
                    f"malformed KeyRing entry {entry!r}: expected '<kid>:<hex32>'"
                )
            kid, _, hex_key = entry.partition(":")
            if not kid:
                raise ArtifactError(
                    f"malformed KeyRing entry {entry!r}: kid must not be empty"
                )
            try:
                key_bytes = bytes.fromhex(hex_key)
            except ValueError as exc:
                raise ArtifactError(
                    f"malformed KeyRing entry for kid {kid!r}: "
                    f"key is not valid hex: {exc}"
                ) from exc
            if len(key_bytes) != 32:
                raise ArtifactError(
                    f"KeyRing entry for kid {kid!r}: key must decode to exactly "
                    f"32 bytes, got {len(key_bytes)}"
                )
            if kid in self._keys:
                raise ArtifactError(
                    f"duplicate kid {kid!r} in KeyRing entries"
                )
            self._keys[kid] = key_bytes
            self._order.append(kid)

    @property
    def active_kid(self) -> str:
        """The kid for the first (active) key; used by the trainer."""
        return self._order[0]

    def get(self, kid: str) -> bytes | None:
        """Return the key bytes for *kid*, or ``None`` if not found.

        Never raises; the caller decides whether a missing kid is an error.
        """
        return self._keys.get(kid)

    def kids(self) -> list[str]:
        """Return all registered kids in insertion order."""
        return list(self._order)

    def fingerprint(self, kid: str) -> str | None:
        """Return ``sha256(key)[:8]`` hex for *kid* (safe to log).

        Returns ``None`` if the kid is not in this KeyRing.
        """
        key = self._keys.get(kid)
        if key is None:
            return None
        return hashlib.sha256(key).hexdigest()[:8]


# ---------------------------------------------------------------------------
# HMAC compute / verify
# ---------------------------------------------------------------------------


def compute_hmac(
    key: bytes,
    kid_bytes: bytes,
    header_json: bytes,
    payload: bytes,
) -> bytes:
    """Compute HMAC-SHA256 over ``kid_bytes || header_json || payload``.

    The HMAC scope deliberately includes the kid so that tampering with the
    kid to redirect verification to a different key will fail verification.
    """
    h = hmac.new(key, digestmod=hashlib.sha256)
    h.update(kid_bytes)
    h.update(header_json)
    h.update(payload)
    return h.digest()


def verify_hmac(
    key_ring: KeyRing,
    kid: str,
    kid_bytes: bytes,
    header_json: bytes,
    payload: bytes,
    stored_digest: bytes,
) -> None:
    """Verify the HMAC stored in an artifact against the key for *kid*.

    Raises ``ArtifactError`` if:
    - *kid* is not present in *key_ring*.
    - The computed digest does not match *stored_digest* (constant-time compare).

    The raw key bytes are never exposed in log events; only the kid is logged.
    """
    key = key_ring.get(kid)
    if key is None:
        logger.warning("artifact_kid_unknown", kid=kid)
        raise ArtifactError(
            f"artifact signed with unknown kid {kid!r}; "
            "check RECOTEM_SIGNING_KEYS configuration"
        )

    expected = compute_hmac(key, kid_bytes, header_json, payload)
    if not hmac.compare_digest(stored_digest, expected):
        logger.warning("artifact_hmac_mismatch", kid=kid)
        raise ArtifactError(
            f"HMAC verification failed for kid {kid!r}; "
            "artifact may have been tampered with"
        )


# ---------------------------------------------------------------------------
# SafeUnpickler
# ---------------------------------------------------------------------------


class SafeUnpickler(pickle.Unpickler):
    """Unpickler that restricts class construction to ``_ALLOWED_CLASSES``.

    Any (module, name) pair not in the allow-list raises ``ArtifactError``
    before the class is instantiated, providing defence in depth independent
    of HMAC verification.
    """

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in _ALLOWED_CLASSES:
            raise ArtifactError(
                f"class not allowed: {module}.{name}; "
                "only the hand-enumerated FQCN list may be constructed"
            )
        return super().find_class(module, name)


def unpickle_payload(payload_bytes: bytes) -> Any:
    """Deserialize *payload_bytes* using ``SafeUnpickler``.

    This is intentionally separate from ``read_artifact`` so that callers
    such as ``recotem inspect`` can read and verify the artifact without
    triggering deserialization.

    Raises ``ArtifactError`` on any disallowed class or pickle decoding error.
    """
    try:
        return SafeUnpickler(io.BytesIO(payload_bytes)).load()
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(f"pickle deserialization failed: {exc}") from exc
