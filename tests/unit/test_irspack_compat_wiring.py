"""Wiring tests for the irspack version-skew guard.

The guard is only useful if its ArtifactError is classified and counted as
``version_skew`` rather than being swallowed by a neighbouring branch. These
tests pin that contract:

- `_classify_artifact_error` maps the skew message to "version_skew" and NOT
  to "parse" (whose catch-all matches any message containing "version").
- "version_skew" is an accepted Prometheus label and is not coerced away.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from recotem._irspack_compat import SKEW_MSG_PREFIX, check_artifact_irspack_version
from recotem.artifact.format import ArtifactError
from recotem.config import ServeConfig
from recotem.serving.app import _try_load_artifact
from recotem.serving.metrics import _LOAD_FAILURE_REASONS
from recotem.serving.watcher import _classify_artifact_error


def _skew_message() -> str:
    """Return a real skew message, produced by the guard itself.

    Built from the guard rather than hand-written so the test cannot drift
    away from the wording the guard actually emits.
    """
    with pytest.raises(ArtifactError) as excinfo:
        check_artifact_irspack_version(
            {"irspack_version": "0.4.2"}, name="news", running="0.5.0"
        )
    return str(excinfo.value)


def test_skew_message_classifies_as_version_skew() -> None:
    assert _classify_artifact_error(_skew_message()) == "version_skew"


def test_skew_message_is_not_misclassified_as_parse() -> None:
    """Regression: the "parse" branch claims any message containing "version".

    The skew message contains "irspack 0.4.2"/"version", so ordering in
    `_classify_artifact_error` is load-bearing. If the skew branch is moved
    below the parse branch, this fails.
    """
    msg = _skew_message()
    assert "version" in msg.lower(), "precondition: message contains 'version'"
    assert _classify_artifact_error(msg) != "parse"


def test_version_skew_is_an_allowed_metric_label() -> None:
    """Otherwise inc_artifact_load_failure silently coerces it to "unexpected"."""
    assert "version_skew" in _LOAD_FAILURE_REASONS


def test_classifier_prefix_matches_guard_prefix() -> None:
    """The classifier keys off the guard's prefix; keep them in sync."""
    assert _skew_message().lower().startswith(SKEW_MSG_PREFIX)


# ---------------------------------------------------------------------------
# Startup path (app.py) -- its reason is a hardcoded literal, not classified
# ---------------------------------------------------------------------------


def _load_with_header(tmp_path: Path, make_artifact, key_ring, header: dict):
    """Run serve's startup loader over an artifact carrying *header*."""
    data = make_artifact(header_dict=header)
    path = tmp_path / "skew.recotem"
    path.write_bytes(data)
    recipe = types.SimpleNamespace(
        name="news",
        output=types.SimpleNamespace(path=str(path)),
        item_metadata=None,
    )
    return _try_load_artifact(recipe, key_ring, ServeConfig())


def test_startup_path_reports_version_skew(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    entry, reason = _load_with_header(
        tmp_path, make_artifact, single_key_ring, {"irspack_version": "0.4.2"}
    )
    assert reason == "version_skew"
    assert entry.loaded is False


def test_skew_remedy_survives_health_error_truncation(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """The remedy must fit in the 200-char `last_load_error` budget.

    `_sanitize_error` truncates to 200 chars, which is what `/health` exposes.
    A message that buries "retrain" past that point is useless exactly where an
    operator reads it, so the guard front-loads the remedy and both versions.
    """
    entry, _ = _load_with_header(
        tmp_path, make_artifact, single_key_ring, {"irspack_version": "0.4.2"}
    )
    surfaced = entry.last_load_error or ""
    assert "retrain" in surfaced.lower(), f"remedy truncated away: {surfaced!r}"
    assert "0.4.2" in surfaced, "artifact's irspack version truncated away"
    assert "news" in surfaced, "recipe name truncated away"


def test_startup_version_skew_reason_is_not_coerced(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """Regression: app.py returns a hardcoded "version_skew" literal.

    Unlike the watcher, this path does not go through
    `_classify_artifact_error`, so nothing else pins the literal. If it drifts
    from `_LOAD_FAILURE_REASONS`, `inc_artifact_load_failure` silently relabels
    the metric to "unexpected" and the skew becomes invisible to alerting.
    """
    _, reason = _load_with_header(
        tmp_path, make_artifact, single_key_ring, {"irspack_version": "0.4.2"}
    )
    assert reason in _LOAD_FAILURE_REASONS


def test_startup_path_passes_guard_on_matching_version(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """A header recording the running irspack must clear the guard.

    The payload is a bare dict rather than a recommender, so the load still
    fails downstream — but it must not fail as `version_skew`.
    """
    import irspack

    _, reason = _load_with_header(
        tmp_path,
        make_artifact,
        single_key_ring,
        {"irspack_version": irspack.__version__},
    )
    assert reason != "version_skew"
