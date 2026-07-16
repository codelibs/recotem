"""Unit tests for recotem._irspack_compat.

Tests:
- Matching major.minor passes
- Patch-level drift is tolerated
- Major/minor skew raises ArtifactError naming the remedy
- Missing / unparseable header version warns but does not block
- RECOTEM_ALLOW_IRSPACK_VERSION_SKEW downgrades the error to a warning
"""

from __future__ import annotations

import pytest

from recotem._irspack_compat import (
    _major_minor,
    _running_irspack_version,
    check_artifact_irspack_version,
)
from recotem.artifact.format import ArtifactError

SKEW_ENV = "RECOTEM_ALLOW_IRSPACK_VERSION_SKEW"


@pytest.fixture(autouse=True)
def _clear_skew_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKEW_ENV, raising=False)


# ---------------------------------------------------------------------------
# Compatible cases
# ---------------------------------------------------------------------------


def test_matching_version_passes() -> None:
    check_artifact_irspack_version(
        {"irspack_version": "0.5.0"}, name="r", running="0.5.0"
    )


@pytest.mark.parametrize("header_version", ["0.5.0", "0.5.1", "0.5.12"])
def test_patch_drift_is_tolerated(header_version: str) -> None:
    """Patch releases do not change the pickle format; only major.minor is compared."""
    check_artifact_irspack_version(
        {"irspack_version": header_version}, name="r", running="0.5.3"
    )


# ---------------------------------------------------------------------------
# Skew cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header_version", "running"),
    [
        ("0.4.2", "0.5.0"),  # the irspack 0.5.0 IALS pickle break
        ("0.5.0", "0.4.2"),  # serve rolled back before artifacts were retrained
        ("1.0.0", "0.5.0"),  # major skew
    ],
)
def test_version_skew_raises_actionable_error(
    header_version: str, running: str
) -> None:
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_irspack_version(
            {"irspack_version": header_version}, name="news", running=running
        )
    msg = str(excinfo.value)
    assert header_version in msg
    assert running in msg
    assert "retrain" in msg.lower()
    assert SKEW_ENV in msg


def test_skew_env_downgrades_to_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SKEW_ENV, "1")
    check_artifact_irspack_version(
        {"irspack_version": "0.4.2"}, name="r", running="0.5.0"
    )


# ---------------------------------------------------------------------------
# Unknown / malformed header version -- must not block the load
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        {},  # pre-2.0 artifact with no version recorded
        {"irspack_version": None},
        {"irspack_version": ""},
        {"irspack_version": "not-a-version"},
        {"irspack_version": "0"},
        {"irspack_version": 42},  # non-string
    ],
)
def test_unknown_header_version_does_not_block(header: dict) -> None:
    """An unverifiable version is not evidence of incompatibility.

    The deserializer remains the backstop; refusing here would strand
    artifacts that predate the header field.
    """
    check_artifact_irspack_version(header, name="r", running="0.5.0")


def test_unparseable_running_version_does_not_block() -> None:
    check_artifact_irspack_version(
        {"irspack_version": "0.5.0"}, name="r", running="weird-dev-build"
    )


# ---------------------------------------------------------------------------
# Default (production) path -- no `running` override
# ---------------------------------------------------------------------------


def test_running_version_resolves_from_installed_irspack() -> None:
    """`_running_irspack_version` must find a real version string.

    It swallows every exception and returns None, which makes the guard fail
    open. Without this test, an upstream rename of `irspack.__version__` would
    silently render the guard inert with a green suite.
    """
    import irspack

    assert _running_irspack_version() == irspack.__version__
    assert _major_minor(_running_irspack_version()) is not None


def test_default_path_detects_skew_against_installed_irspack() -> None:
    """The guard must work without an injected `running` -- as serve calls it."""
    import irspack

    installed = _major_minor(irspack.__version__)
    assert installed is not None
    skewed = f"{installed[0]}.{installed[1] + 1}.0"

    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_irspack_version({"irspack_version": skewed}, name="r")
    assert irspack.__version__ in str(excinfo.value)


def test_default_path_accepts_installed_version() -> None:
    import irspack

    check_artifact_irspack_version({"irspack_version": irspack.__version__}, name="r")
