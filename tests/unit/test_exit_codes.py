"""Unit tests for recotem._exit_codes._map_exception_to_exit.

Tests:
- every exception class maps to the documented exit code (CLI-3)
- HttpFetchError is picked up via __cause__ chain when wrapped by DataSourceError
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map(exc):
    from recotem._exit_codes import _map_exception_to_exit

    return _map_exception_to_exit(exc)


# ---------------------------------------------------------------------------
# CLI-3: parametrized exit-code table verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_factory,expected_code",
    [
        # RecipeError → 2
        (
            lambda: __import__(
                "recotem.recipe.errors", fromlist=["RecipeError"]
            ).RecipeError("bad recipe"),
            2,
        ),
        # DataSourceError → 3
        (
            lambda: __import__(
                "recotem.datasource.base", fromlist=["DataSourceError"]
            ).DataSourceError("fetch failed"),
            3,
        ),
        # TrainingError (no code) → 4
        (
            lambda: __import__(
                "recotem.training.errors", fromlist=["TrainingError"]
            ).TrainingError("evaluation failed"),
            4,
        ),
        # ArtifactError → 5
        (
            lambda: __import__(
                "recotem.artifact.format", fromlist=["ArtifactError"]
            ).ArtifactError("bad magic"),
            5,
        ),
        # LockContestedError → 6
        (
            lambda: __import__(
                "recotem.training.lock", fromlist=["LockContestedError"]
            ).LockContestedError("locked"),
            6,
        ),
        # HttpFetchError → 7
        (
            lambda: __import__(
                "recotem._http_fetch", fromlist=["HttpFetchError"]
            ).HttpFetchError("SSRF"),
            7,
        ),
        # ConfigError → 8
        (
            lambda: __import__("recotem.config", fromlist=["ConfigError"]).ConfigError(
                "missing key"
            ),
            8,
        ),
        # TrainingError(code="signing_key_missing") → 8
        (
            lambda: __import__(
                "recotem.training.errors", fromlist=["TrainingError"]
            ).TrainingError("keys missing", code="signing_key_missing"),
            8,
        ),
        # Unknown Exception → 1
        (
            lambda: RuntimeError("unexpected"),
            1,
        ),
        # Unknown KeyError → 1
        (
            lambda: KeyError("missing"),
            1,
        ),
    ],
    ids=[
        "RecipeError→2",
        "DataSourceError→3",
        "TrainingError→4",
        "ArtifactError→5",
        "LockContestedError→6",
        "HttpFetchError→7",
        "ConfigError→8",
        "TrainingError_signing_key_missing→8",
        "RuntimeError→1",
        "KeyError→1",
    ],
)
def test_map_exception_to_exit_table(exc_factory, expected_code: int) -> None:
    """Every documented exception class maps to its canonical exit code."""
    exc = exc_factory()
    assert _map(exc) == expected_code, (
        f"{type(exc).__name__} must map to exit {expected_code}; got {_map(exc)}"
    )


# ---------------------------------------------------------------------------
# CLI-3: __cause__ chain walking for HttpFetchError wrapped in DataSourceError
# ---------------------------------------------------------------------------


def test_map_exception_to_exit_http_fetch_via_cause_chain() -> None:
    """DataSourceError wrapping HttpFetchError via __cause__ must map to exit 7.

    CronJob retry logic distinguishes transient HTTP/SSRF failures (7) from
    structural datasource failures (3) by inspecting the exit code.  The
    _map_exception_to_exit function must walk the __cause__ chain to find
    the underlying HttpFetchError.
    """
    from recotem._exit_codes import _EXIT_HTTP_FETCH
    from recotem._http_fetch import HttpFetchError
    from recotem.datasource.base import DataSourceError

    inner = HttpFetchError("private IP blocked by SSRF guard")
    try:
        raise DataSourceError("HTTP source fetch failed") from inner
    except DataSourceError as exc:
        wrapped = exc

    result = _map(wrapped)
    assert result == _EXIT_HTTP_FETCH, (
        f"DataSourceError wrapping HttpFetchError must map to exit {_EXIT_HTTP_FETCH} "
        f"(HttpFetchError); got {result}"
    )


def test_map_exception_to_exit_datasource_without_http_cause_maps_to_3() -> None:
    """Plain DataSourceError (no HttpFetchError in cause chain) maps to exit 3."""
    from recotem.datasource.base import DataSourceError

    exc = DataSourceError("auth token expired — no HTTP cause")
    assert _map(exc) == 3, f"Plain DataSourceError must map to exit 3; got {_map(exc)}"


def test_map_exception_to_exit_deep_cause_chain() -> None:
    """HttpFetchError buried two levels deep in the cause chain is still exit 7."""
    from recotem._exit_codes import _EXIT_HTTP_FETCH
    from recotem._http_fetch import HttpFetchError
    from recotem.datasource.base import DataSourceError

    inner = HttpFetchError("scheme-changing redirect blocked")
    try:
        raise RuntimeError("intermediate wrapper") from inner
    except RuntimeError as mid:
        intermediate = mid

    try:
        raise DataSourceError("outer wrapper") from intermediate
    except DataSourceError as outer:
        exc = outer

    result = _map(exc)
    assert result == _EXIT_HTTP_FETCH, (
        f"HttpFetchError two levels deep must still map to {_EXIT_HTTP_FETCH}; "
        f"got {result}"
    )
