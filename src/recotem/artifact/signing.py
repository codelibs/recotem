"""HMAC-SHA256 signing, verification, and safe deserialization for Recotem artifacts.

Security posture
----------------
Pickle is the irspack-native serialization format and cannot be avoided for
scipy sparse matrices and numpy arrays.  The risk is mitigated by four
independent, layered controls:

1. Strong magic / version / size checks before any deserialization (format.py).
2. HMAC-SHA256 signature verification with multi-kid support and constant-time
   compare via ``hmac.compare_digest``; signing keys are never logged (only
   the kid is surfaced in log events).
3. Hand-enumerated FQCN allow-list in ``SafeUnpickler.find_class`` -- an RCE
   backstop that is independent of the HMAC.  Augmented by a narrow
   module-prefix allow-list scoped to ``numpy.*`` and ``scipy.sparse.*``
   (numpy / scipy reshuffle their reconstruction helpers across releases,
   so a strict FQCN list would break on every dep bump); a deny-list
   removes the high-risk submodules within those prefixes.  See
   ``docs/security.md`` for the full threat model.
4. Required signing key for both train and serve; a misconfigured deployment
   fails closed rather than loading arbitrary files.

Key rotation
------------
``RECOTEM_SIGNING_KEYS`` is a comma-separated list of ``<kid>:<hex64>``
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
from typing import Any

import structlog

from recotem._log_safe import format_kid_for_log
from recotem.artifact.format import ArtifactError

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FQCN allow-list (hand-enumerated; see docs/security.md)
# ---------------------------------------------------------------------------

_ALLOWED_CLASSES: frozenset[tuple[str, str]] = frozenset(
    {
        # Recotem neutral wrapper.  Pickle records the class's defining module;
        # since 2.0.0a0 this is recotem._idmap (package-level, independent of
        # training or serving sub-packages).  The old paths
        # (recotem.training._compat, recotem.serving._compat) are NOT in the
        # allow-list — artifacts from earlier commits cannot be loaded, which
        # is acceptable for a pre-release alpha.
        ("recotem._idmap", "IDMappedRecommender"),
        # irspack id mapping
        ("irspack.utils.id_mapping", "IDMapper"),
        # irspack recommenders.  Pickle records the original defining
        # submodule, not the package re-export.  The set is frozen per
        # release and updated when irspack adds / renames recommenders.
        ("irspack.recommenders.ials", "IALSRecommender"),
        ("irspack.recommenders.knn", "CosineKNNRecommender"),
        ("irspack.recommenders.toppop", "TopPopRecommender"),
        ("irspack.recommenders.rp3", "RP3betaRecommender"),
        ("irspack.recommenders.dense_slim", "DenseSLIMRecommender"),
        ("irspack.recommenders.truncsvd", "TruncatedSVDRecommender"),
        ("irspack.recommenders.bpr", "BPRFMRecommender"),
        # numpy.  Both numpy 1.x (numpy.core.*) and numpy 2.x
        # (numpy._core.*) reconstruction helpers are pinned explicitly
        # — these are the FQCNs every artifact references via the
        # _reconstruct / scalar reduce helpers.
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy.core.multiarray", "scalar"),
        ("numpy._core.multiarray", "_reconstruct"),
        ("numpy._core.multiarray", "scalar"),
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


# Module-prefix allow-list for scientific computing libraries.
#
# numpy and scipy reorganise their internal layout between releases — the
# pickle reconstruction helpers (``_reconstruct``, ``scalar``) and the
# dtype factories (``numpy.dtypes.Float64DType`` and friends) move between
# submodules across major versions, so a strict FQCN-only list would break
# on every dep bump.  We therefore allow a *narrow* set of submodule
# prefixes that contain only reconstruction / dtype-factory helpers:
#
#   numpy._core.       numpy 2.x reconstruction helpers + scalar / dtype
#                      machinery (multiarray._reconstruct, ``numeric``…).
#   numpy.core.        numpy 1.x equivalents kept for forward compat with
#                      pre-2.x artifacts.
#   numpy.dtypes.      numpy 2.x parametric dtype classes
#                      (Float64DType, BoolDType, …) referenced by ndarray
#                      reconstruction.
#   scipy.sparse._csr. CSR matrix reconstructor + helpers.
#   scipy.sparse._csc. CSC equivalent.
#   scipy.sparse._coo. COO equivalent.
#
# Bare-module entries (``numpy``, ``scipy.sparse``) are intentionally NOT
# on the prefix list — top-level numpy gadgets such as ``numpy.frompyfunc``,
# ``numpy.vectorize``, ``numpy.piecewise`` and ``scipy.sparse.load_npz``
# (file-IO) are not needed for Recotem artifacts and are blocked.  The
# legitimate top-level FQCNs (``numpy.ndarray``, ``numpy.dtype``) are
# pinned by the hand-enumerated ``_ALLOWED_CLASSES`` set above.
#
# HMAC verification remains the primary defence; this prefix list is the
# secondary layer scoped to the scientific stack only.
_ALLOWED_MODULE_PREFIXES: tuple[str, ...] = (
    "numpy._core.",
    "numpy.core.",
    "numpy.dtypes.",
    "scipy.sparse._csr.",
    "scipy.sparse._csc.",
    "scipy.sparse._coo.",
)

# Denied submodules that fall under an allowed prefix but expose
# code-execution gadgets or risky helpers (test runners, build helpers,
# foreign function bindings, code generators, callable proxies, file-IO
# constructors).  Matched as exact module strings or with a trailing dot
# to denote the full subtree.  Deny overrides the prefix allow.
_DENIED_MODULE_PREFIXES: tuple[str, ...] = (
    # numpy: test runners, build / FFI / code-gen helpers, file-IO + callable
    # proxies in numpy.lib (DataSource, open_memmap, etc.), legacy shims.
    "numpy.testing",
    "numpy.testing.",
    "numpy.distutils",
    "numpy.distutils.",
    "numpy.f2py",
    "numpy.f2py.",
    "numpy.ctypeslib",
    "numpy.ctypeslib.",
    "numpy.lib",
    "numpy.lib.",
    "numpy.compat",
    "numpy.compat.",
    # numpy.random: RNG state and bit-generator state (PCG64, MT19937, etc.)
    # are not needed in Recotem artifacts.  Denied defensively because a future
    # numpy release could introduce a reduce-callable in the random module that
    # carries side-effects.  Any legitimate RNG class needed by a future irspack
    # version should be added by exact FQCN to _ALLOWED_CLASSES rather than
    # widening this deny-list (prefer explicit allow over implicit leak).
    "numpy.random",
    "numpy.random.",
    # numpy._core._exceptions: internal exception hierarchy; not referenced by
    # any irspack / scipy reconstruction path.  Denied to shrink the internal
    # attack surface exposed through the broad numpy._core.* prefix allow-list
    # (the prefix only permits reconstruction helpers and dtype factories).
    "numpy._core._exceptions",
    "numpy._core._exceptions.",
    # scipy.sparse: linalg.LinearOperator accepts an arbitrary callable
    # (matvec=...), test runner internals, csgraph C extensions.  Recotem
    # payloads only need csr / csc / coo from scipy.sparse._{csr,csc,coo}.
    "scipy.sparse.linalg",
    "scipy.sparse.linalg.",
    "scipy.sparse.tests",
    "scipy.sparse.tests.",
    "scipy.sparse.csgraph",
    "scipy.sparse.csgraph.",
)


def _module_matches(module: str, patterns: tuple[str, ...]) -> bool:
    for p in patterns:
        if p.endswith(".") and module.startswith(p):
            return True
        if not p.endswith(".") and module == p:
            return True
    return False


def _is_allowed(module: str, name: str) -> bool:
    # Deny-list is checked first: a future allow-list addition must never
    # accidentally re-permit a denied submodule.  The HMAC verify is the
    # primary defence; this is the secondary RCE backstop.
    if _module_matches(module, _DENIED_MODULE_PREFIXES):
        return False
    if (module, name) in _ALLOWED_CLASSES:
        return True
    return _module_matches(module, _ALLOWED_MODULE_PREFIXES)


# ---------------------------------------------------------------------------
# KeyRing
# ---------------------------------------------------------------------------


class KeyRing:
    """Immutable map from kid to 32-byte HMAC key.

    Construction
    ------------
    Pass one or more ``"<kid>:<hex64>"`` strings — i.e. a kid followed by
    64 hex chars that decode to 32 raw bytes (the format used by
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
                    f"malformed KeyRing entry {entry!r}: expected '<kid>:<hex64>'"
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
                logger.warning(
                    "signing_keyring_invalid",
                    reason="duplicate_kid",
                    kid=format_kid_for_log(kid),
                )
                raise ArtifactError(f"duplicate kid {kid!r} in KeyRing entries")
            # Foot-gun guard: kids are expected to be short human labels
            # (e.g. ``prod-2026``, ``dev``).  A kid that looks like raw
            # key material — 32 or more hex chars — strongly suggests the
            # operator pasted the signing key bytes into the kid field by
            # mistake.  Refuse to construct rather than risk leaking key
            # material via the kid log field (the redaction rule only
            # scrubs hex64-shaped values that appear in unrelated string
            # fields; structured ``kid=...`` fields pass through as-is).
            if len(kid) >= 32 and all(c in "0123456789abcdefABCDEF" for c in kid):
                logger.warning(
                    "signing_keyring_invalid",
                    reason="kid_looks_like_hex_key_material",
                    kid=kid[:8] + "...",
                )
                raise ArtifactError(
                    f"KeyRing entry has a kid {kid[:8]}... that looks like "
                    "raw hex key material (>=32 hex chars).  Use a short "
                    "human label (e.g. 'prod-2026') for the kid; the "
                    "secret bytes belong AFTER the colon."
                )
            self._keys[kid] = key_bytes
            self._order.append(kid)

        # Emit audit log so operators can confirm which keys are loaded at
        # startup without exposing any key material (only fingerprint prefix).
        logger.info(
            "signing_keyring_built",
            n_keys=len(self._order),
            active_kid=format_kid_for_log(self._order[0]),
            fingerprints=[
                {"kid": format_kid_for_log(k), "fingerprint": self.fingerprint(k)}
                for k in self._order
            ],
        )

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
        logger.warning("artifact_kid_unknown", kid=format_kid_for_log(kid))
        raise ArtifactError(
            f"artifact signed with unknown kid {kid!r}; "
            "check RECOTEM_SIGNING_KEYS configuration"
        )

    expected = compute_hmac(key, kid_bytes, header_json, payload)
    if not hmac.compare_digest(stored_digest, expected):
        logger.warning("artifact_hmac_mismatch", kid=format_kid_for_log(kid))
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
        if not _is_allowed(module, name):
            raise ArtifactError(
                f"class not allowed: {module}.{name}; "
                "only the hand-enumerated FQCN list (and the numpy / "
                "scipy.sparse module-prefix allow-list) may be constructed"
            )
        return super().find_class(module, name)


def unpickle_payload(payload_bytes: bytes) -> Any:
    """Deserialize *payload_bytes* using ``SafeUnpickler``.

    This is intentionally separate from ``read_artifact`` so that callers
    such as ``recotem inspect`` can read and verify the artifact without
    triggering deserialization.

    Raises ``ArtifactError`` on any disallowed class or deserialization error.
    ``MemoryError`` and ``RecursionError`` are re-raised unwrapped so OOM /
    stack-exhaustion is not swallowed as ``ArtifactError`` in the watcher loop
    (M-8).
    """
    try:
        return SafeUnpickler(io.BytesIO(payload_bytes)).load()
    except ArtifactError:
        raise
    except (MemoryError, RecursionError):
        raise  # OOM/stack-exhaustion must not be swallowed into ArtifactError
    except ImportError as exc:
        # An allow-listed FQCN referenced a module that is not installed in
        # this environment (e.g. an irspack recommender pinned to a version
        # that the serving process does not have).  This is operationally
        # distinct from a disallowed FQCN (RCE backstop) -- operators must
        # install the missing dependency rather than edit the allow-list.
        logger.warning(
            "safe_unpickle_module_missing",
            error_class=type(exc).__name__,
            error=str(exc),
        )
        raise ArtifactError(
            f"required module unavailable during deserialization: {exc}. "
            "Install the matching recotem extras / irspack version on the "
            "serving host."
        ) from exc
    except (AttributeError, TypeError) as exc:
        # Programming error / dependency version mismatch -- the full stack
        # trace is required for diagnosis.  Log at exception level (includes
        # traceback) and re-raise the original exception so the caller can
        # distinguish "dep incompatibility" (AttributeError/TypeError) from
        # "corrupt bytes" (ArtifactError).
        logger.exception(
            "safe_unpickle_internal_error",
            error_class=type(exc).__name__,
        )
        raise
    except (pickle.UnpicklingError, EOFError, ValueError) as exc:
        # True binary corruption or truncated stream -- map to ArtifactError so
        # the caller can surface a user-visible "artifact damaged" message.
        raise ArtifactError(f"deserialization failed: {exc}") from exc
    except Exception as exc:
        # Catch-all for unexpected exception types (e.g. RuntimeError from a
        # third-party codec).  Map to ArtifactError to prevent an unhandled
        # exception from leaking internal details.
        raise ArtifactError(f"deserialization failed: {exc}") from exc
