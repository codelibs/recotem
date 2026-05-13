"""Unit tests for recotem.recipe.envvars — env-var expansion.

Covers sil M-14: empty env-var value emits a WARNING log event
'recipe_env_var_empty' but the expansion still returns the empty string
(existing behaviour is preserved, only observation is added).
"""

from __future__ import annotations

import structlog.testing

from recotem.recipe.envvars import expand_env_vars


def test_expand_empty_env_var_emits_warning(monkeypatch: object) -> None:
    """Expanding a RECOTEM_RECIPE_ variable that is set to '' must emit
    recipe_env_var_empty at WARNING level."""
    import os

    monkeypatch.setitem(os.environ, "RECOTEM_RECIPE_EMPTY_FIELD", "")  # type: ignore[attr-defined]

    with structlog.testing.capture_logs() as cap:
        result = expand_env_vars("prefix-${RECOTEM_RECIPE_EMPTY_FIELD}-suffix")

    # The expansion still returns the empty string (behaviour preserved).
    assert result == "prefix--suffix", (
        f"Expected 'prefix--suffix' after expansion; got {result!r}"
    )

    warn_events = [e for e in cap if e.get("event") == "recipe_env_var_empty"]
    assert warn_events, (
        "Expected 'recipe_env_var_empty' warning event; "
        f"got events: {[e.get('event') for e in cap]}"
    )
    assert warn_events[0]["log_level"] == "warning", (
        f"Expected log_level='warning'; got {warn_events[0].get('log_level')!r}"
    )
    assert warn_events[0]["name"] == "RECOTEM_RECIPE_EMPTY_FIELD"


def test_expand_non_empty_env_var_does_not_emit_warning(
    monkeypatch: object,
) -> None:
    """Expanding a variable with a non-empty value must NOT emit any warning."""
    import os

    monkeypatch.setitem(os.environ, "RECOTEM_RECIPE_NONEMPTY", "hello")  # type: ignore[attr-defined]

    with structlog.testing.capture_logs() as cap:
        result = expand_env_vars("${RECOTEM_RECIPE_NONEMPTY}")

    assert result == "hello"
    warn_events = [e for e in cap if e.get("event") == "recipe_env_var_empty"]
    assert not warn_events, f"Non-empty var must not emit warning; got {warn_events!r}"


def test_expand_empty_env_var_via_extra_allowed_emits_warning() -> None:
    """extra_allowed override with empty string must also emit the warning."""
    with structlog.testing.capture_logs() as cap:
        result = expand_env_vars(
            "${RECOTEM_RECIPE_OVERRIDE}",
            extra_allowed={"RECOTEM_RECIPE_OVERRIDE": ""},
        )

    assert result == ""
    warn_events = [e for e in cap if e.get("event") == "recipe_env_var_empty"]
    assert warn_events, "Expected warning for empty extra_allowed value"
    assert warn_events[0]["name"] == "RECOTEM_RECIPE_OVERRIDE"
