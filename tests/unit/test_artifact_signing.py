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
