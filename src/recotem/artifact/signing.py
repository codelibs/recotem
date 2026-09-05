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
from recotem.config import ConfigError

logger = structlog.get_logger(__name__)


class KeyRingConfigError(ArtifactError, ConfigError):
    """Raised for a malformed ``RECOTEM_SIGNING_KEYS`` value.

    A bad key entry is an operator typo in the environment, not a corrupt
    artifact: exit 8 (configuration), never exit 5 (retrain).  ``RECOTEM_API_KEYS``
    already behaves this way; this type removes the asymmetry on the signing side.

    Inherits from both so that existing callers catching :class:`ArtifactError`
    on the signing path keep working, while ``_map_exception_to_exit`` — which
    checks ``ConfigError`` before ``ArtifactError`` — routes it to
    ``_EXIT_CONFIG``.  ``code`` is picked up by ``training.pipeline``'s
    ``train_error`` event so a config typo is not logged as ``internal_error``
    with a stack trace.
    """

    code = "signing_keys_invalid"


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
        # irspack recommender *internals*.  A trained recommender is not a
        # single object: the pickle graph also embeds the trainer, config, and
        # enum instances the recommender holds as attributes.  These FQCNs are
        # NOT covered by listing the top-level *Recommender class, so omitting
        # them makes the artifact unloadable at serve time even though training
        # + signing succeeded (the recommender pickles fine, but SafeUnpickler
        # rejects the embedded class on load).  Enumerated by training each
        # algorithm and recording every find_class the payload triggers; see
        # tests/unit/test_artifact_signing.py::test_<algo>_artifact_roundtrip.
        #
        # IALS — IALSRecommender embeds an IALSTrainer plus its model/solver
        # config dataclasses and Loss/Solver enums (defined in the compiled
        # _ials_core extension), and an IALSConfigScaling enum.
        ("irspack.recommenders.ials", "IALSTrainer"),
        ("irspack.recommenders.ials", "IALSConfigScaling"),
        ("irspack.recommenders._ials_core", "IALSTrainer"),
        ("irspack.recommenders._ials_core", "IALSModelConfig"),
        ("irspack.recommenders._ials_core", "IALSSolverConfig"),
        ("irspack.recommenders._ials_core", "LossType"),
        ("irspack.recommenders._ials_core", "SolverType"),
        # CosineKNN — stores the feature-weighting scheme as an enum.
        ("irspack.recommenders.knn", "FeatureWeightingScheme"),
        # TruncatedSVD — irspack delegates to scikit-learn's TruncatedSVD,
        # whose fitted estimator is embedded in the recommender.  Its numpy
        # array attributes are already covered by the numpy.* prefix list; only
        # the estimator class itself needs an explicit FQCN entry.
        ("sklearn.decomposition._truncated_svd", "TruncatedSVD"),
        # BPRFM — irspack's early-stopping base keeps the fitted trainer as an
        # attribute (``get_score`` reads ``self.trainer.fm``), so the payload
        # embeds BPRFMTrainer and, through it, the LightFM model object itself.
        # Listing only BPRFMRecommender above made `recotem train` succeed and
        # sign an artifact that `recotem serve` then refused to deserialize.
        ("irspack.recommenders.bpr", "BPRFMTrainer"),
        ("lightfm.lightfm", "LightFM"),
        # (BPRFM also needs five numpy.random RNG-state helpers, which live
        # under a deny-prefix — see _DENY_PREFIX_EXEMPTIONS below.)
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
    # NOTE: "numpy.dtypes." is deliberately absent.  A trailing dot only
    # matches sub-modules, and ``numpy.dtypes`` has none, so the entry that
    # used to sit here matched nothing.  numpy does not reference
    # ``numpy.dtypes.*`` FQCNs from its own pickles either -- arrays and
    # dtypes round-trip through ``numpy.dtype`` plus ``_frombuffer`` -- so
    # nothing needs it today.  If a future numpy does emit them, add the
    # individual classes to _ALLOWED_CLASSES rather than widening the prefix
    # list to the whole module (which also carries two non-class callables).
    "scipy.sparse._csr.",
    "scipy.sparse._csc.",
    "scipy.sparse._coo.",
)

# Leaf names the module-prefix allow-list may admit.
#
# A module-prefix match by itself would permit EVERY attribute of EVERY
# importable submodule under the prefix -- 896 callables under ``numpy._core.``
# alone, including code-execution primitives that have nothing to do with
# reconstruction: ``numpy._core._multiarray_tests.npy_import_entry_point``
# (a getattr-by-string that returns any ``module:attr``, e.g. ``os.system``,
# as a value -- a laundry that walks arbitrary callables straight past this
# allow-list, exactly like the dotted-name bypass closed in #202), and
# ``numpy._core.memmap.memmap`` (an arbitrary file create/truncate primitive).
# The prefix list exists only to absorb cross-version *module* moves of a
# handful of numpy/scipy reconstruction helpers whose *names* are stable, so
# gate it on those names.  A name that is not a known reconstruction helper is
# refused even under an allowed prefix -- fail-closed; a genuinely new helper
# name is added here (or its class to _ALLOWED_CLASSES) with justification.
_ALLOWED_PREFIX_NAMES: frozenset[str] = frozenset(
    {
        # numpy ndarray / scalar reconstruction helpers.  These are the entry
        # points every ndarray pickle goes through; their defining submodule
        # has moved across releases (numpy.core.multiarray -> numpy._core.*),
        # which is the whole reason for a prefix rather than an exact list.
        "_reconstruct",
        "scalar",
        "_frombuffer",
        # scipy sparse matrix classes.  Also pinned exactly in _ALLOWED_CLASSES;
        # kept here so a future scipy that relocates them under a matched
        # prefix still loads.  Data constructors, no callable argument.
        "csr_matrix",
        "csc_matrix",
        "coo_matrix",
    }
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

# The only FQCNs that outrank _DENIED_MODULE_PREFIXES.
#
# Kept separate from _ALLOWED_CLASSES on purpose.  If exact entries in that set
# simply beat the deny-list, then every one of its ~40 entries — and every one
# added later — would silently gain the power to re-open a denied subtree, and
# the deny-list would stop being a floor.  Routing the exceptions through their
# own small set means a reviewer sees "this bypasses the deny-list" in the diff
# instead of having to notice that a new tuple happens to fall under a denied
# prefix.
#
# Every entry must state which artifact needs it and why the class is safe.
# "Safe" here means: the class is a data constructor, it does not accept a
# caller-supplied callable, and it was observed in a real payload — not
# inferred from source.
_DENY_PREFIX_EXEMPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # BPRFM.  LightFM seeds itself with a numpy RandomState and keeps it as
        # an attribute, so the trainer embedded in every BPRFM artifact drags
        # in the RNG-state pickle graph.  These five were enumerated by loading
        # an actual BPRFM artifact and recording each rejected find_class.
        #
        # All five reconstruct RNG *state*: the two _pickle ctors take a
        # bit-generator name (looked up in a module-level dict) and return a
        # fresh RandomState / BitGenerator, MT19937 is the Mersenne Twister
        # itself, and the SeedSequence pair carries the entropy tuple.  None
        # takes a callable, so none is a gadget.  The deny-prefix continues to
        # cover the rest of numpy.random.
        ("numpy.random._pickle", "__randomstate_ctor"),
        ("numpy.random._pickle", "__bit_generator_ctor"),
        ("numpy.random._mt19937", "MT19937"),
        ("numpy.random.bit_generator", "__pyx_unpickle_SeedSequence"),
        ("numpy.random.bit_generator", "SeedSequence"),
    }
)


def _module_matches(module: str, patterns: tuple[str, ...]) -> bool:
    for p in patterns:
        if p.endswith(".") and module.startswith(p):
            return True
        if not p.endswith(".") and module == p:
            return True
    return False


def _is_allowed(module: str, name: str) -> bool:
    # A dotted *name* is always a traversal attempt.  ``Unpickler.find_class``
    # resolves protocol-4 ``STACK_GLOBAL`` names with ``_getattribute``, which
    # walks dots, so ``(numpy._core._methods, "os.system")`` would name an
    # allow-listed module and still resolve to the real ``os.system``.  Every
    # legitimate entry is a plain identifier -- asserted by
    # ``test_allowed_class_names_are_plain_identifiers`` -- so rejecting dots
    # closes the traversal without narrowing what genuine artifacts may load.
    if "." in name:
        return False
    # The narrow exemption set is consulted before the deny-list -- and it is
    # the ONLY thing that outranks it.  Keeping it separate from
    # _ALLOWED_CLASSES is deliberate: the deny-list stays a hard floor for all
    # 40-odd ordinary allow-list entries, so a careless future addition still
    # cannot re-open numpy.lib's file-IO constructors or scipy.sparse.linalg's
    # arbitrary-callable LinearOperator.  Only the handful of FQCNs written
    # into _DENY_PREFIX_EXEMPTIONS, each with its own justification, get past
    # it.  See test_is_allowed_deny_takes_precedence_over_exact_allow_list_entry.
    if (module, name) in _DENY_PREFIX_EXEMPTIONS:
        return True
    # Deny-list is checked next: a future allow-list addition must never
    # accidentally re-permit a denied submodule.  The HMAC verify is the
    # primary defence; this is the secondary RCE backstop.
    if _module_matches(module, _DENIED_MODULE_PREFIXES):
        return False
    if (module, name) in _ALLOWED_CLASSES:
        return True
    # A prefix match alone would admit every attribute of every submodule under
    # the prefix (getattr-by-string / file-IO gadgets included); restrict it to
    # the stable reconstruction-helper names the prefix exists to carry.
    if name not in _ALLOWED_PREFIX_NAMES:
        return False
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
            raise KeyRingConfigError("KeyRing requires at least one key entry")

        # Accept a single comma-separated string as a convenience
        flat: list[str] = []
        for entry in entries:
            flat.extend(e.strip() for e in entry.split(",") if e.strip())

        if not flat:
            raise KeyRingConfigError("KeyRing requires at least one key entry")

        self._keys: dict[str, bytes] = {}
        self._order: list[str] = []

        for entry in flat:
            if ":" not in entry:
                raise KeyRingConfigError(
                    f"malformed KeyRing entry {entry!r}: expected '<kid>:<hex64>'"
                )
            kid, _, hex_key = entry.partition(":")
            if not kid:
                raise KeyRingConfigError(
                    f"malformed KeyRing entry {entry!r}: kid must not be empty"
                )
            try:
                key_bytes = bytes.fromhex(hex_key)
            except ValueError as exc:
                raise KeyRingConfigError(
                    f"malformed KeyRing entry for kid {kid!r}: "
                    f"key is not valid hex: {exc}"
                ) from exc
            if len(key_bytes) != 32:
                raise KeyRingConfigError(
                    f"KeyRing entry for kid {kid!r}: key must decode to exactly "
                    f"32 bytes, got {len(key_bytes)}"
                )
            if kid in self._keys:
                logger.warning(
                    "signing_keyring_invalid",
                    reason="duplicate_kid",
                    kid=format_kid_for_log(kid),
                )
                raise KeyRingConfigError(f"duplicate kid {kid!r} in KeyRing entries")
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
                raise KeyRingConfigError(
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
    """Unpickler that restricts class construction to what ``_is_allowed`` permits.

    That is ``_ALLOWED_CLASSES``, the narrow ``numpy`` / ``scipy.sparse``
    module-prefix allow-list, and ``_DENY_PREFIX_EXEMPTIONS`` -- minus anything
    matching ``_DENIED_MODULE_PREFIXES``.  Any other (module, name) pair raises
    ``ArtifactError`` before the class is instantiated, providing defence in
    depth independent of HMAC verification.
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
