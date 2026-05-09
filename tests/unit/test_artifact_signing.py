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
        "numpy.foo",
        "numpy._core.multiarray",
        "numpy._core.numeric",
        "numpy.dtypes",
        "numpy.fft",
        "numpy.linalg",
        "numpy.random",
    ],
)
def test_module_prefix_allow_numpy_subpaths(module: str) -> None:
    """numpy sub-modules are allowed unless they fall under a denied prefix."""
    from recotem.artifact.signing import _is_allowed

    # Use an innocuous name that is not in _ALLOWED_CLASSES so we exercise
    # the prefix path only (not the exact-match path).
    assert _is_allowed(module, "_reconstruct") is True


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
        "scipy.sparse.foo",
        "scipy.sparse._compressed",
        "scipy.sparse._data_matrix",
    ],
)
def test_module_prefix_allow_scipy_sparse_subpaths(module: str) -> None:
    """scipy.sparse sub-modules are allowed via the prefix allow-list."""
    from recotem.artifact.signing import _is_allowed

    assert _is_allowed(module, "some_internal") is True


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
