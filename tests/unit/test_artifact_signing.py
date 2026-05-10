"""Unit tests for recotem.artifact.signing.

Tests:
- Sign+verify roundtrip
- One-byte payload tamper rejection
- HMAC with wrong key rejected
- Unknown kid rejected
- SafeUnpickler allow-list (parameterised gadget rejection)
- KeyRing rotation semantics
- Module-prefix allow-list (_module_matches, _is_allowed)
- Denied numpy sub-trees (testing, distutils, f2py, ctypeslib)
"""

from __future__ import annotations

import pytest

from recotem.artifact.format import ArtifactError
from recotem.artifact.signing import (
    KeyRing,
    SafeUnpickler,
    compute_hmac,
    unpickle_payload,
    verify_hmac,
)
from tests.conftest import ACTIVE_KEY_HEX, OLD_KEY_HEX

# ---------------------------------------------------------------------------
# KeyRing construction
# ---------------------------------------------------------------------------


def test_key_ring_active_kid_is_first() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")
    assert kr.active_kid == "active"


def test_key_ring_get_known_kid_returns_bytes() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    assert kr.get("active") == bytes.fromhex(ACTIVE_KEY_HEX)


def test_key_ring_get_unknown_kid_returns_none() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    assert kr.get("nonexistent") is None


def test_key_ring_kids_returns_all_in_order() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")
    assert kr.kids() == ["active", "old"]


def test_key_ring_duplicate_kid_raises() -> None:
    with pytest.raises(ArtifactError, match="duplicate"):
        KeyRing(f"active:{ACTIVE_KEY_HEX}", f"active:{OLD_KEY_HEX}")


def test_key_ring_empty_raises() -> None:
    with pytest.raises(ArtifactError):
        KeyRing()


def test_key_ring_malformed_entry_raises() -> None:
    with pytest.raises(ArtifactError, match="malformed"):
        KeyRing("no-colon-here")


def test_key_ring_wrong_key_length_raises() -> None:
    with pytest.raises(ArtifactError, match="32 bytes"):
        KeyRing("kid:" + "aa" * 16)  # only 16 bytes


def test_key_ring_comma_separated_single_string() -> None:
    """KeyRing accepts a comma-separated string as a single argument."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX},old:{OLD_KEY_HEX}")
    assert kr.kids() == ["active", "old"]


def test_key_ring_fingerprint_returns_8_hex_chars() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    fp = kr.fingerprint("active")
    assert fp is not None
    assert len(fp) == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_key_ring_fingerprint_unknown_kid_returns_none() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    assert kr.fingerprint("ghost") is None


# ---------------------------------------------------------------------------
# Sign + verify roundtrip
# ---------------------------------------------------------------------------


def test_sign_verify_roundtrip() -> None:
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"test"}'
    payload = b"arbitrary payload"
    key = kr.get(kid)
    assert key is not None
    digest = compute_hmac(key, kid_bytes, header_json, payload)
    verify_hmac(kr, kid, kid_bytes, header_json, payload, digest)  # no exception


def test_one_byte_tamper_rejected() -> None:
    """Flipping one byte in payload causes verify to fail."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"test"}'
    payload = bytearray(b"original payload bytes")
    key = kr.get(kid)
    assert key is not None
    digest = compute_hmac(key, kid_bytes, header_json, bytes(payload))
    payload[0] ^= 0xFF  # flip one byte
    with pytest.raises(ArtifactError, match="HMAC"):
        verify_hmac(kr, kid, kid_bytes, header_json, bytes(payload), digest)


def test_hmac_valid_over_wrong_key_rejected() -> None:
    """Digest computed with a different key fails verify."""
    kr_correct = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kr_wrong = KeyRing(f"active:{OLD_KEY_HEX}")  # same kid, different key
    kid = "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"x":1}'
    payload = b"data"
    key_wrong = kr_wrong.get(kid)
    assert key_wrong is not None
    bad_digest = compute_hmac(key_wrong, kid_bytes, header_json, payload)
    with pytest.raises(ArtifactError, match="HMAC"):
        verify_hmac(kr_correct, kid, kid_bytes, header_json, payload, bad_digest)


def test_hmac_valid_with_unknown_kid_rejected() -> None:
    """Artifact signed with a kid not in the KeyRing raises ArtifactError."""
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")
    kid = "ghost"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"x":1}'
    payload = b"data"
    fake_digest = b"\x00" * 32
    with pytest.raises(ArtifactError, match="unknown kid"):
        verify_hmac(kr, kid, kid_bytes, header_json, payload, fake_digest)


# ---------------------------------------------------------------------------
# KeyRing rotation: old key still verifies
# ---------------------------------------------------------------------------


def test_old_key_verifies_with_two_key_ring() -> None:
    """An artifact signed with the old key verifies against a two-key ring."""
    kr_old_only = KeyRing(f"old:{OLD_KEY_HEX}")
    kid = "old"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"legacy"}'
    payload = b"payload"
    key_old = kr_old_only.get(kid)
    assert key_old is not None
    digest = compute_hmac(key_old, kid_bytes, header_json, payload)

    kr_both = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")
    verify_hmac(kr_both, kid, kid_bytes, header_json, payload, digest)  # no exception


# ---------------------------------------------------------------------------
# SafeUnpickler allow-list (parameterised gadget rejection)
# ---------------------------------------------------------------------------

_GADGETS = [
    ("os", "system"),
    ("subprocess", "Popen"),
    ("numpy.testing", "run_module_suite"),
    ("builtins", "exec"),
    ("posix", "system"),
]


@pytest.mark.parametrize("module,name", _GADGETS)
def test_payload_class_outside_whitelist_rejected(module: str, name: str) -> None:
    """SafeUnpickler.find_class rejects classes not in the allow-list."""
    import io

    unpickler = SafeUnpickler(io.BytesIO(b""))
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class(module, name)


def test_safe_unpickler_allows_builtins_dict() -> None:
    """builtins.dict is in the allow-list and must not be blocked."""
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    result = unpickle_payload(payload)
    assert result == {"x": 1}


def test_safe_unpickler_allows_builtins_list() -> None:
    """builtins.list is in the allow-list."""
    import pickle  # noqa: S403

    payload = pickle.dumps([1, 2, 3], protocol=4)  # noqa: S301
    result = unpickle_payload(payload)
    assert result == [1, 2, 3]


def test_unpickle_with_disallowed_class_raises_artifact_error() -> None:
    """A pickle stream referencing a disallowed class raises ArtifactError."""
    # Build a pickle stream that calls os.system
    import os as _os
    import pickle  # noqa: S403

    class _Exploit:
        def __reduce__(self):
            return (_os.system, ("echo pwned",))

    payload = pickle.dumps(_Exploit(), protocol=4)  # noqa: S301
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickle_payload(payload)


# ---------------------------------------------------------------------------
# Module-prefix allow-list: _module_matches helper
# ---------------------------------------------------------------------------


def test_module_matches_helper_with_trailing_dot() -> None:
    """A pattern ending in '.' matches any module that starts with that prefix."""
    from recotem.artifact.signing import _module_matches

    assert _module_matches("numpy.core.multiarray", ("numpy.",)) is True
    assert _module_matches("numpy._core.numeric", ("numpy.",)) is True
    # Does not match an unrelated module even if it starts with the same letters.
    assert _module_matches("numpyextension.foo", ("numpy.",)) is False


def test_module_matches_helper_without_trailing_dot() -> None:
    """A pattern without trailing '.' matches only the exact module string."""
    from recotem.artifact.signing import _module_matches

    assert _module_matches("numpy", ("numpy",)) is True
    assert _module_matches("numpy.core", ("numpy",)) is False  # must be exact
    assert _module_matches("numpyx", ("numpy",)) is False


# ---------------------------------------------------------------------------
# Module-prefix allow-list: _is_allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        # numpy._core.* — numpy 2.x reconstruction helpers
        "numpy._core.multiarray",
        "numpy._core.numeric",
        "numpy._core.fromnumeric",
        # numpy.core.* — numpy 1.x reconstruction helpers (forward-compat)
        "numpy.core.multiarray",
        "numpy.core.numeric",
        # numpy.dtypes.* — parametric dtype factories
        "numpy.dtypes.Float64DType",
        "numpy.dtypes.BoolDType",
    ],
)
def test_module_prefix_allow_numpy_subpaths(module: str) -> None:
    """numpy reconstruction-helper / dtype submodules are allowed via prefix.

    The prefix list is intentionally narrow: only ``numpy._core.``,
    ``numpy.core.`` and ``numpy.dtypes.`` are allowed.  Other numpy
    submodules (``numpy.fft``, ``numpy.linalg``, ``numpy.random``,
    ``numpy.foo`` …) are NOT permitted via the prefix list — see
    :func:`test_module_prefix_rejects_other_numpy_submodules`.
    """
    from recotem.artifact.signing import _is_allowed

    # Use an innocuous name that is not in _ALLOWED_CLASSES so we exercise
    # the prefix path only (not the exact-match path).
    assert _is_allowed(module, "_reconstruct") is True


@pytest.mark.parametrize(
    "module",
    [
        # Top-level numpy (bare module) is NOT on the prefix list —
        # the legitimate top-level FQCNs (numpy.ndarray, numpy.dtype) are
        # pinned by the hand-enumerated _ALLOWED_CLASSES set instead, so
        # `numpy.frompyfunc`, `numpy.vectorize`, `numpy.piecewise` and the
        # like are blocked.
        "numpy",
        # Other numpy submodules outside the narrow allow-list.
        "numpy.foo",
        "numpy.fft",
        "numpy.linalg",
        "numpy.random",
        "numpy.polynomial",
    ],
)
def test_module_prefix_rejects_other_numpy_submodules(module: str) -> None:
    """numpy submodules outside numpy._core / numpy.core / numpy.dtypes are
    not whitelisted via the prefix list — only an exact FQCN match in
    ``_ALLOWED_CLASSES`` lets a name through.
    """
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed(module, "_some_helper") is False


def test_module_prefix_deny_numpy_lib_overrides_allow() -> None:
    """numpy.lib has DataSource / open_memmap / utils gadgets and is denied
    even though the broader numpy.* prefix is allowed."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.lib", "DataSource") is False
    assert _is_allowed("numpy.lib.npyio", "open_memmap") is False
    assert _is_allowed("numpy.lib.format", "_read_array_header_2_0") is False


def test_module_prefix_deny_numpy_compat() -> None:
    """numpy.compat is a legacy shim subtree and is denied."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.compat", "asbytes") is False
    assert _is_allowed("numpy.compat.py3k", "asunicode") is False


@pytest.mark.parametrize(
    "module",
    [
        # Only scipy.sparse._csr / _csc / _coo and submodules are allowed.
        "scipy.sparse._csr",
        "scipy.sparse._csc",
        "scipy.sparse._coo",
    ],
)
def test_module_prefix_rejects_scipy_sparse_bare_submodules(module: str) -> None:
    """The narrow prefix list permits only ``scipy.sparse._{csr,csc,coo}.``
    *children*; the modules themselves require an exact FQCN match in
    ``_ALLOWED_CLASSES``.  ``scipy.sparse`` (bare) and other submodules
    such as ``_compressed`` / ``_data_matrix`` / ``foo`` are rejected.
    """
    from recotem.artifact.signing import _is_allowed

    # Bare modules: rejected (only their FQCN entries pass)
    assert _is_allowed(module, "_some_helper") is False


@pytest.mark.parametrize(
    "module",
    [
        # children of the narrow allow-prefixes are accepted via prefix
        "scipy.sparse._csr.foo",
        "scipy.sparse._csc.bar",
        "scipy.sparse._coo.baz",
    ],
)
def test_module_prefix_allow_scipy_sparse_csr_csc_coo_children(module: str) -> None:
    """Children of the narrow scipy.sparse._{csr,csc,coo}. prefixes pass."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed(module, "_internal_helper") is True


@pytest.mark.parametrize(
    "module",
    [
        # scipy.sparse bare module — must NOT be accepted (was accidentally
        # accepted by the previous broad ``scipy.sparse.*`` prefix).
        "scipy.sparse",
        "scipy.sparse._compressed",
        "scipy.sparse._data_matrix",
        "scipy.sparse.foo",
    ],
)
def test_module_prefix_rejects_other_scipy_sparse_submodules(module: str) -> None:
    """Other scipy.sparse submodules not in {_csr, _csc, _coo} are rejected."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed(module, "_some") is False


def test_module_prefix_deny_scipy_sparse_linalg() -> None:
    """scipy.sparse.linalg is denied: LinearOperator accepts arbitrary callables."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("scipy.sparse.linalg", "LinearOperator") is False
    assert _is_allowed("scipy.sparse.linalg.eigen.arpack", "_arpack") is False


def test_module_prefix_deny_scipy_sparse_tests() -> None:
    """scipy.sparse.tests is the test runner subtree and is denied."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("scipy.sparse.tests", "test_base") is False
    assert _is_allowed("scipy.sparse.tests.test_csr", "TestCSR") is False


def test_module_prefix_deny_scipy_sparse_csgraph() -> None:
    """scipy.sparse.csgraph C extensions are denied even though core sparse is allowed."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("scipy.sparse.csgraph", "shortest_path") is False
    assert _is_allowed("scipy.sparse.csgraph._traversal", "_traverse") is False


def test_module_prefix_deny_numpy_testing_overrides_allow() -> None:
    """numpy.testing is in the denied list even though numpy.* is broadly allowed."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.testing", "run_module_suite") is False
    assert _is_allowed("numpy.testing.decorators", "knownfailureif") is False


def test_module_prefix_deny_numpy_distutils() -> None:
    """numpy.distutils sub-tree is denied (build helper, not safe)."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.distutils", "exec_command") is False
    assert _is_allowed("numpy.distutils.command", "build_clib") is False


def test_module_prefix_deny_numpy_f2py() -> None:
    """numpy.f2py sub-tree is denied (code-generator gadget)."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.f2py", "run_main") is False
    assert _is_allowed("numpy.f2py.crackfortran", "crackline") is False


def test_module_prefix_deny_numpy_ctypeslib() -> None:
    """numpy.ctypeslib is denied (foreign function bindings)."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.ctypeslib", "load_library") is False
    assert _is_allowed("numpy.ctypeslib._ctypes_loader", "_something") is False


def test_module_prefix_does_not_match_unrelated_modules() -> None:
    """Unrelated modules (requests, os) are not allowed via the prefix list."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("requests", "get") is False
    assert _is_allowed("os", "system") is False
    assert _is_allowed("requests.adapters", "HTTPAdapter") is False
    assert _is_allowed("subprocess", "Popen") is False


def test_supported_algorithms_match_unpickler_allow_list() -> None:
    """Every algorithm class the trainer claims to support must be loadable
    by the SafeUnpickler.  Drift between SUPPORTED_CLASS_NAMES and
    _ALLOWED_CLASSES is a CRITICAL bug — the artifact passes HMAC verify
    and then dies during deserialize.

    If this test fails, either:
      - add the missing FQCN to recotem.artifact.signing._ALLOWED_CLASSES, or
      - remove the algorithm from recotem.training.algorithms.SUPPORTED_CLASS_NAMES.
    """
    from recotem.artifact.signing import _ALLOWED_CLASSES
    from recotem.training.algorithms import SUPPORTED_CLASS_NAMES

    allowed_names = {name for _module, name in _ALLOWED_CLASSES}
    missing = SUPPORTED_CLASS_NAMES - allowed_names
    assert not missing, (
        f"trainer registers algorithms whose pickled classes the SafeUnpickler "
        f"will reject: {sorted(missing)}"
    )


def test_bprfm_class_is_explicitly_allowed() -> None:
    """Regression: BPRFM was registered in SUPPORTED_CLASS_NAMES but missing
    from the allow-list, breaking every BPRFM artifact at deserialize time."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("irspack.recommenders.bpr", "BPRFMRecommender") is True


# ---------------------------------------------------------------------------
# T-4: End-to-end SafeUnpickler rejection for denied numpy submodules
# ---------------------------------------------------------------------------
# Tests call SafeUnpickler.find_class directly — the same hook invoked for
# every GLOBAL/REDUCE opcode during deserialization.  This confirms the
# protection applies through the full find_class code path, independent of
# _is_allowed unit tests.


def test_safe_unpickler_rejects_numpy_lib_via_find_class() -> None:
    """SafeUnpickler must raise ArtifactError for numpy.lib classes.

    numpy.lib contains file-IO and callable-proxy gadgets (DataSource,
    open_memmap, _read_array_header_2_0, …).  It is explicitly denied even
    though the broader numpy.* prefix is allowed.
    """
    import io

    unpickler = SafeUnpickler(io.BytesIO(b""))

    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class("numpy.lib", "DataSource")

    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class("numpy.lib.format", "_read_array_header_2_0")

    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class("numpy.lib.npyio", "open_memmap")


def test_safe_unpickler_rejects_numpy_compat_via_find_class() -> None:
    """SafeUnpickler must raise ArtifactError for numpy.compat classes.

    numpy.compat is a legacy shim tree; it is explicitly denied.
    """
    import io

    unpickler = SafeUnpickler(io.BytesIO(b""))

    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class("numpy.compat", "asbytes")

    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class("numpy.compat.py3k", "asunicode")


def test_safe_unpickler_rejects_numpy_lib_via_raw_opcode_stream() -> None:
    """Verify end-to-end rejection via a hand-built serialization opcode stream.

    Constructs a minimal binary stream that instructs the deserializer to
    load numpy.lib.DataSource — a denied class — without actually importing
    DataSource.  This confirms find_class fires and raises ArtifactError
    before any object is instantiated.

    Uses raw opcode bytes (no third-party serialization library) so the test
    is independent of any helper that itself uses the deserialization format.
    """
    import io
    import struct

    # Raw opcode constants (protocol 4)
    # 0x80 = PROTO, 0x95 = FRAME, ord('c') = GLOBAL opcode, ord('.') = STOP
    PROTO_OPCODE = b"\x80\x04"
    FRAME_OPCODE = b"\x95"
    GLOBAL_OPCODE = b"c"
    STOP_OPCODE = b"."

    module_name = b"numpy.lib"
    class_name = b"DataSource"
    body = GLOBAL_OPCODE + module_name + b"\n" + class_name + b"\n" + STOP_OPCODE
    stream = PROTO_OPCODE + FRAME_OPCODE + struct.pack("<Q", len(body)) + body

    with pytest.raises(ArtifactError, match="not allowed"):
        SafeUnpickler(io.BytesIO(stream)).load()


# ---------------------------------------------------------------------------
# H1. kid bytes tamper rejected
# ---------------------------------------------------------------------------


def test_idmap_module_imports_without_training_compat() -> None:
    """``from recotem._idmap import IDMappedRecommender`` must succeed even
    if ``recotem.training._compat`` (where the IPython stub historically lived)
    has not been imported yet.

    Regression: irspack pulls in fastprogress at import time, which imports
    ``IPython.display``.  The stub used to live only in
    ``recotem.training._compat``, so importing ``recotem._idmap`` first (e.g.
    from a serving-only context) raised ``ModuleNotFoundError: No module named
    'IPython'``.  ``recotem._idmap`` must self-bootstrap the stub.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from recotem._idmap import IDMappedRecommender; "
            "assert IDMappedRecommender.__module__ == 'recotem._idmap'",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Direct import of recotem._idmap failed: stderr={result.stderr!r}"
    )


def test_idmap_neutral_fqcn_in_allow_list() -> None:
    """recotem._idmap.IDMappedRecommender must be in the FQCN allow-list.

    The old paths (recotem.training._compat, recotem.serving._compat) must NOT
    be present -- artifacts using those FQCNs are intentionally broken in
    2.0.0a0 to enforce the clean neutral-module migration.
    """
    from recotem.artifact.signing import _ALLOWED_CLASSES

    assert ("recotem._idmap", "IDMappedRecommender") in _ALLOWED_CLASSES, (
        "recotem._idmap.IDMappedRecommender must be in _ALLOWED_CLASSES"
    )
    assert (
        "recotem.training._compat",
        "IDMappedRecommender",
    ) not in _ALLOWED_CLASSES, (
        "recotem.training._compat.IDMappedRecommender must NOT be in _ALLOWED_CLASSES "
        "(old pre-2.0.0a0 path)"
    )
    assert ("recotem.serving._compat", "IDMappedRecommender") not in _ALLOWED_CLASSES, (
        "recotem.serving._compat.IDMappedRecommender must NOT be in _ALLOWED_CLASSES "
        "(old cross-package re-export path)"
    )


def test_kid_bytes_tampered_rejected() -> None:
    """Replacing the kid field bytes in a valid artifact (keeping same length)
    must cause HMAC verification to fail with ArtifactError.

    The HMAC scope includes kid_bytes || header_json || payload, so swapping
    the kid bytes from 'active' to a different 6-byte string invalidates the
    digest even if both kids are registered in the KeyRing.

    We parse the tampered bytes directly with parse_header_from_bytes and then
    call verify_hmac manually to stay independent of the I/O layer.
    """
    from recotem.artifact.format import parse_header_from_bytes
    from recotem.artifact.signing import KeyRing
    from recotem.artifact.signing import verify_hmac as _verify_hmac
    from tests.conftest import ACTIVE_KEY_HEX, OLD_KEY_HEX, build_raw_artifact

    # Build a valid artifact signed with 'active'
    raw = build_raw_artifact("active", ACTIVE_KEY_HEX)

    # Locate the kid bytes in the artifact.
    # Layout: MAGIC(8) + VERSION+RESERVED(4) + KID_LEN(1) + kid_bytes(K) + HMAC(32) + ...
    kid_len_offset = 12  # 8 (MAGIC) + 2 (VERSION) + 2 (RESERVED)
    kid_len = raw[kid_len_offset]  # should be 6 for "active"
    assert kid_len == 6, f"expected kid length 6 for 'active', got {kid_len}"

    kid_start = kid_len_offset + 1
    kid_end = kid_start + kid_len

    # Original kid is b"active" (6 bytes).  Replace with b"stoxxx" (also 6 bytes).
    # Both are registered in the two-kid KeyRing below, but the HMAC was computed
    # over b"active", so the mismatch must be detected.
    tampered = bytearray(raw)
    assert raw[kid_start:kid_end] == b"active"
    tampered[kid_start:kid_end] = b"stoxxx"
    tampered_bytes = bytes(tampered)

    # Build a KeyRing that has both 'active' and 'stoxxx' so the kid lookup in
    # verify_hmac succeeds, but the HMAC will fail because the scope is different.
    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}", f"stoxxx:{OLD_KEY_HEX}")

    # Parse the tampered artifact header
    hdr = parse_header_from_bytes(tampered_bytes, max_payload_bytes=10 * 1024 * 1024)
    payload = tampered_bytes[hdr.payload_offset :]

    # verify_hmac must reject: the stored digest was computed over b"active",
    # but the kid field now reads b"stoxxx", so the scope diverges.
    with pytest.raises(ArtifactError, match="HMAC"):
        _verify_hmac(
            kr,
            hdr.kid,
            hdr.kid.encode("utf-8"),
            hdr.header_data,
            payload,
            hdr.hmac_digest,
        )


# ---------------------------------------------------------------------------
# MAJOR-1: top-level numpy / scipy.sparse gadgets that the OLD broad prefix
# would have allowed but the NEW narrow prefix rejects.  These are not used by
# Recotem artifacts but they have callable / file-IO surface area which would
# otherwise sit inside the secondary defence-in-depth perimeter.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module,name",
    [
        # numpy: callable proxies + ufunc factories.  Not in any release of
        # numpy ever needed for recotem artifacts.
        ("numpy", "frompyfunc"),
        ("numpy", "vectorize"),
        ("numpy", "piecewise"),
        # scipy.sparse top-level helpers (file-IO; reads attacker-controlled
        # bytes from disk through scipy's own loader path).
        ("scipy.sparse", "load_npz"),
        ("scipy.sparse", "save_npz"),
    ],
)
def test_safe_unpickler_rejects_topmodule_gadgets(module: str, name: str) -> None:
    """Top-level numpy / scipy.sparse gadgets that are NOT in the FQCN
    allow-list must be rejected.  The hand-enumerated FQCN list pins the
    legitimate top-level entries (numpy.ndarray / numpy.dtype) — anything
    else lands on the prefix path and is denied because the narrow prefix
    list only covers submodules such as ``numpy._core.`` / ``numpy.core.`` /
    ``numpy.dtypes.`` / ``scipy.sparse._{csr,csc,coo}.``
    """
    import io

    from recotem.artifact.signing import SafeUnpickler, _is_allowed

    # Exact-prefix path
    assert _is_allowed(module, name) is False

    # End-to-end via SafeUnpickler.find_class (the GLOBAL hook used by every
    # REDUCE opcode in the unpickler — confirms the rejection happens at the
    # actual load-time hook, not just the helper).
    unpickler = SafeUnpickler(io.BytesIO(b""))
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class(module, name)


def test_safe_unpickler_rejects_numpy_random() -> None:
    """numpy.random is no longer accepted via prefix; bit-generators expose
    a rich state-restoration API that does not need to be unpicklable in
    Recotem artifacts.
    """
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("numpy.random", "PCG64") is False
    assert _is_allowed("numpy.random._pickle", "__bit_generator_ctor") is False


def test_module_prefix_allows_numpy_core_reconstruct() -> None:
    """Both numpy 1.x (``numpy.core.multiarray._reconstruct``) and numpy 2.x
    (``numpy._core.multiarray._reconstruct``) reconstruction helpers are
    allowed — they are the entry points every ndarray pickle goes through.
    """
    from recotem.artifact.signing import _is_allowed

    # FQCN exact match (also covered by the prefix path)
    assert _is_allowed("numpy.core.multiarray", "_reconstruct") is True
    assert _is_allowed("numpy._core.multiarray", "_reconstruct") is True
    assert _is_allowed("numpy.core.multiarray", "scalar") is True
    assert _is_allowed("numpy._core.multiarray", "scalar") is True


def test_module_prefix_allows_scipy_sparse_reconstructors() -> None:
    """The CSR / CSC / COO matrix classes load through the FQCN allow-list."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed("scipy.sparse._csr", "csr_matrix") is True
    assert _is_allowed("scipy.sparse._csc", "csc_matrix") is True
    assert _is_allowed("scipy.sparse._coo", "coo_matrix") is True


# ---------------------------------------------------------------------------
# CRITICAL-5: FQCN allow-list violation via real I/O path
# ---------------------------------------------------------------------------
# These tests build a signed artifact whose payload contains a disallowed class
# and verify that unpickle_payload propagates ArtifactError.


def _build_disallowed_artifact(disallowed_module: str, disallowed_name: str) -> bytes:
    """Build a signed artifact whose payload embeds a reference to a disallowed class.

    The HMAC is computed over valid kid||header||payload bytes so HMAC
    verification passes.  The disallowed-class guard must fire during
    unpickle_payload, not during HMAC verification.
    """
    import hmac as _hmac
    import json
    import struct

    from recotem.artifact.format import FORMAT_VERSION, MAGIC

    # Build a minimal pickle opcode stream that references the disallowed FQCN.
    # SafeUnpickler.find_class raises ArtifactError before any import/execution.
    GLOBAL_OPCODE = b"c"
    STOP_OPCODE = b"."
    PROTO_OPCODE = b"\x80\x04"
    FRAME_OPCODE = b"\x95"

    global_body = (
        GLOBAL_OPCODE
        + disallowed_module.encode("utf-8")
        + b"\n"
        + disallowed_name.encode("utf-8")
        + b"\n"
        + STOP_OPCODE
    )
    payload_bytes = (
        PROTO_OPCODE + FRAME_OPCODE + struct.pack("<Q", len(global_body)) + global_body
    )

    kid = "active"
    kid_bytes = kid.encode("utf-8")
    key_bytes = bytes.fromhex(ACTIVE_KEY_HEX)
    header_dict = {
        "recipe_name": "disallowed_fqcn_test",
        "best_class": disallowed_name,
        "best_score": 0.0,
        "trained_at": "2026-01-01T00:00:00Z",
    }
    header_json = json.dumps(header_dict, separators=(",", ":")).encode("utf-8")

    h = _hmac.new(key_bytes, digestmod="sha256")
    h.update(kid_bytes)
    h.update(header_json)
    h.update(payload_bytes)
    digest = h.digest()

    kid_len = len(kid_bytes)
    header_len = len(header_json)

    parts: list[bytes] = [
        MAGIC,
        struct.pack("<HH", FORMAT_VERSION, 0),
        bytes([kid_len]),
        kid_bytes,
        digest,
        struct.pack("<I", header_len),
        header_json,
        payload_bytes,
    ]
    return b"".join(parts)


@pytest.mark.parametrize(
    "disallowed_module,disallowed_name",
    [
        ("os", "system"),
        ("subprocess", "Popen"),
        ("builtins", "exec"),
    ],
)
def test_disallowed_fqcn_in_real_artifact_raises_artifact_error_on_unpack(
    disallowed_module: str,
    disallowed_name: str,
    tmp_path,
) -> None:
    """A signed artifact containing a disallowed FQCN must raise ArtifactError
    when unpickle_payload is called after HMAC verification passes.

    Verifies the full I/O path:
    1. _build_disallowed_artifact -> valid signed bytes with disallowed payload
    2. read_artifact -> parses header + HMAC-verifies (must PASS)
    3. unpickle_payload(payload_bytes) -> must raise ArtifactError
    """
    from recotem.artifact.format import ArtifactError
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload

    artifact_bytes = _build_disallowed_artifact(disallowed_module, disallowed_name)
    artifact_path = tmp_path / "disallowed.recotem"
    artifact_path.write_bytes(artifact_bytes)

    kr = KeyRing(f"active:{ACTIVE_KEY_HEX}")

    # HMAC verification must pass (the artifact is correctly signed)
    header, payload_bytes = read_artifact(str(artifact_path), kr)
    assert header.kid == "active"

    # Deserialization must be refused by the allow-list
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickle_payload(payload_bytes)


# ---------------------------------------------------------------------------
# CRITICAL: KeyRing forward rotation — new key signs, old key only verifies old
# ---------------------------------------------------------------------------


def test_keyring_forward_rotation_new_key_signs_verifies() -> None:
    """After rotation, new key signs; old-only ring rejects new artifacts.

    Scenario:
    1. Build a ring with active=NEW_KEY, old=OLD_KEY.
    2. Sign with compute_hmac using the new (active) key.
    3. Confirm the stored kid is 'active' (the new key's kid).
    4. Confirm verify_hmac succeeds with the two-key ring.
    5. Confirm a ring with ONLY the old key REJECTS the new artifact.

    This complements test_old_key_verifies_with_two_key_ring (backward test).
    """
    # Use fresh keys distinct from conftest fixtures to avoid cross-test state.
    new_key_hex = "cc" * 32  # 64 hex chars = 32 bytes
    old_key_hex = OLD_KEY_HEX  # reuse bb*32 from conftest

    kr_both = KeyRing(f"active:{new_key_hex}", f"old:{old_key_hex}")
    assert kr_both.active_kid == "active"

    kid = kr_both.active_kid  # "active"
    kid_bytes = kid.encode("utf-8")
    header_json = b'{"recipe_name":"rotation_test"}'
    payload = b"forward_rotation_payload"

    # 1. Sign with the active (new) key.
    new_key_bytes = kr_both.get(kid)
    assert new_key_bytes is not None
    digest = compute_hmac(new_key_bytes, kid_bytes, header_json, payload)

    # 2. The kid stored is "active".
    assert kid == "active", f"active kid must be 'active', got {kid!r}"

    # 3. Verify with the two-key ring succeeds.
    verify_hmac(kr_both, kid, kid_bytes, header_json, payload, digest)

    # 4. Removing old key still permits forward verify (kid='active' uses new key).
    kr_new_only = KeyRing(f"active:{new_key_hex}")
    verify_hmac(kr_new_only, kid, kid_bytes, header_json, payload, digest)

    # 5. A ring with ONLY the old key REJECTS the artifact signed by the new key.
    #    Both share kid="active", but the old-only ring maps it to old_key_hex.
    #    verify_hmac must raise because the digest was computed over new_key_hex.
    kr_old_only = KeyRing(f"active:{old_key_hex}")
    from recotem.artifact.format import ArtifactError

    with pytest.raises(ArtifactError, match="HMAC"):
        verify_hmac(kr_old_only, kid, kid_bytes, header_json, payload, digest)


# ---------------------------------------------------------------------------
# M-6 regression: deny-list precedence over allow-list in _is_allowed
# ---------------------------------------------------------------------------


def test_is_allowed_deny_takes_precedence_over_exact_allow_list_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A class whose (module, name) is in _ALLOWED_CLASSES but whose module is
    also in _DENIED_MODULE_PREFIXES MUST be rejected.

    This guards against a future allow-list addition accidentally re-permitting
    a denied submodule.  The deny check must run before the exact-match check.

    Scenario: monkeypatch _ALLOWED_CLASSES to include
    ("numpy.testing._private", "Tester") -- numpy.testing is in
    _DENIED_MODULE_PREFIXES -- and assert that _is_allowed returns False.
    """
    import io

    from recotem.artifact.signing import (
        _ALLOWED_CLASSES,
        SafeUnpickler,
        _is_allowed,
    )

    # The entry to inject: module is in the deny-prefix list even after the
    # allow-list injection, so it must still be rejected.
    poisoned_entry = ("numpy.testing._private", "Tester")

    # Monkeypatch _ALLOWED_CLASSES to include the entry.
    patched = _ALLOWED_CLASSES | {poisoned_entry}
    monkeypatch.setattr("recotem.artifact.signing._ALLOWED_CLASSES", patched)

    # _is_allowed must reject despite the exact FQCN match.
    module, name = poisoned_entry
    result = _is_allowed(module, name)
    assert result is False, (
        f"_is_allowed must return False for {module!r}.{name!r} because the module "
        "is in _DENIED_MODULE_PREFIXES -- deny takes precedence over allow-list."
    )

    # SafeUnpickler.find_class must also raise for the same entry.
    unpickler = SafeUnpickler(io.BytesIO(b""))
    with pytest.raises(ArtifactError, match="not allowed"):
        unpickler.find_class(module, name)
