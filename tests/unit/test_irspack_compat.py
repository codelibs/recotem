"""Unit tests for recotem._irspack_compat.

The guard is an allow-list: a (best_class, version-transition) pair loads only
if it is recorded as empirically verified. These tests pin that semantics --
notably ``test_unverified_future_transition_refused_for_proven_class``, which
fails if the guard is ever weakened into a deny-list.

Tests:
- The five verified-compatible classes load across 0.4 <-> 0.5, both directions
- IALS is refused both directions; BPRFM is refused as unverified
- An unverified future transition is refused even for a verified class
- Missing / unknown best_class on a real skew is refused (fail closed)
- Matching major.minor passes; patch-level drift is tolerated
- Missing / unparseable versions warn but do not block (fail open)
- RECOTEM_ALLOW_IRSPACK_VERSION_SKEW downgrades the error to a warning
- The error survives /health/details' 200-char truncation, measured
"""

from __future__ import annotations

import pytest

from recotem._irspack_compat import (
    SKEW_MSG_PREFIX,
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


_PROVEN_CLASSES = [
    "CosineKNNRecommender",
    "TopPopRecommender",
    "RP3betaRecommender",
    "DenseSLIMRecommender",
    "TruncatedSVDRecommender",
]

_ZERO_FOUR_FIVE = [("0.4.2", "0.5.0"), ("0.5.0", "0.4.2")]


@pytest.mark.parametrize("best_class", _PROVEN_CLASSES)
@pytest.mark.parametrize(("header_version", "running"), _ZERO_FOUR_FIVE)
def test_verified_compatible_classes_load_across_0_4_and_0_5(
    best_class: str, header_version: str, running: str
) -> None:
    """The five classes empirically proven to load bit-exact must not be refused.

    Verified in both directions with irspack as the only variable; see the
    module docstring of ``recotem._irspack_compat`` for what "verified" means.
    """
    check_artifact_irspack_version(
        {"irspack_version": header_version, "best_class": best_class},
        name="r",
        running=running,
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


@pytest.mark.parametrize(("header_version", "running"), _ZERO_FOUR_FIVE)
def test_ials_refused_in_both_directions(header_version: str, running: str) -> None:
    """IALS is the one class empirically proven to break across 0.4/0.5.

    ``IALSModelConfig.__setstate__`` arity went 7 -> 10 at 0.5.0, and the
    failure is symmetric: neither direction restores.
    """
    with pytest.raises(ArtifactError):
        check_artifact_irspack_version(
            {"irspack_version": header_version, "best_class": "IALSRecommender"},
            name="r",
            running=running,
        )


def test_bprfm_refused_because_unverified() -> None:
    """BPRFM is gated behind the separately installed ``lightfm`` package, so
    we have no evidence either way.

    Absence from the table means "unproven", and the allow-list refuses the
    unproven. A deny-list would have silently green-lit this.
    """
    with pytest.raises(ArtifactError):
        check_artifact_irspack_version(
            {"irspack_version": "0.4.2", "best_class": "BPRFMRecommender"},
            name="r",
            running="0.5.0",
        )


@pytest.mark.parametrize("best_class", _PROVEN_CLASSES)
def test_unverified_future_transition_refused_for_proven_class(
    best_class: str,
) -> None:
    """Proven across 0.4<->0.5 does NOT generalise to 0.5<->0.6.

    This pins allow-list (not deny-list) semantics: the table grants a class
    passage across a *specific* transition, never blanket immunity. If the
    guard is ever flipped to "deny IALS only", this test must fail.
    """
    with pytest.raises(ArtifactError):
        check_artifact_irspack_version(
            {"irspack_version": "0.5.0", "best_class": best_class},
            name="r",
            running="0.6.0",
        )


@pytest.mark.parametrize(
    "header",
    [
        {"irspack_version": "0.4.2"},  # best_class absent entirely
        {"irspack_version": "0.4.2", "best_class": None},
        {"irspack_version": "0.4.2", "best_class": "SomePluginRecommender"},
        {"irspack_version": "0.4.2", "best_class": 42},  # non-str
        {"irspack_version": "0.4.2", "best_class": ["unhashable"]},
    ],
)
def test_missing_or_unknown_best_class_refused_on_real_skew(header: dict) -> None:
    """A best_class we cannot key on simply misses the table -> refuse.

    Fail-CLOSED is intentional here, and is the opposite of the fail-OPEN
    treatment of an absent *version* (which leaves nothing to compare at all).
    An unhashable best_class must miss the table, not raise TypeError.
    """
    with pytest.raises(ArtifactError):
        check_artifact_irspack_version(header, name="r", running="0.5.0")


def test_skew_message_is_honest_about_why_it_refused() -> None:
    """The reason is "not on the verified list", not "irspack always breaks".

    Under allow-list semantics most refusals are unproven-not-broken, so the
    message must not overclaim. It cites IALS as the one concrete breakage.
    """
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_irspack_version(
            {"irspack_version": "0.4.2", "best_class": "BPRFMRecommender"},
            name="r",
            running="0.5.0",
        )
    msg = str(excinfo.value)
    assert "verified" in msg.lower()
    assert "IALS" in msg
    assert SKEW_ENV in msg


@pytest.mark.parametrize("name", ["r", "n" * 64])
def test_skew_message_survives_health_truncation(name: str) -> None:
    """/health/details truncates to 200 chars; the essentials must precede it.

    Measured against the real ``_sanitize_error`` rather than eyeballed, at the
    longest recipe name the schema permits (``^[A-Za-z0-9_-]{1,64}$``) paired
    with the longest known class name on a transition that is actually refused.
    """
    from recotem.serving.app import _sanitize_error

    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_irspack_version(
            {"irspack_version": "0.5.0", "best_class": "TruncatedSVDRecommender"},
            name=name,
            running="0.6.0",
        )

    surfaced = _sanitize_error(str(excinfo.value))
    assert len(surfaced) <= 200
    assert surfaced.lower().startswith(SKEW_MSG_PREFIX.lower())
    assert "retrain" in surfaced.lower()
    assert name in surfaced
    assert "TruncatedSVDRecommender" in surfaced
    assert "0.5.0" in surfaced
    assert "0.6.0" in surfaced


def test_overlong_best_class_cannot_push_versions_out_of_budget() -> None:
    """A pathological best_class must not evict the versions from the budget.

    ``best_class`` is header content. It is HMAC-verified before this runs, so
    this is defence in depth rather than a live attack path, but the 200-char
    guarantee should hold on message shape alone and not on trusting the
    header to be well-formed.
    """
    from recotem.serving.app import _sanitize_error

    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_irspack_version(
            {"irspack_version": "0.4.2", "best_class": "X" * 500},
            name="n" * 64,
            running="0.5.0",
        )

    surfaced = _sanitize_error(str(excinfo.value))
    assert len(surfaced) <= 200
    assert "retrain" in surfaced.lower()
    assert "0.4.2" in surfaced
    assert "0.5.0" in surfaced


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
