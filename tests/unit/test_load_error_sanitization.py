"""``last_load_error`` must be sanitized on every path that writes it.

Truncation (200 chars) plus URI redaction used to be a call-site discipline
that only ``serving/app.py``'s startup loader observed.  The watcher's
hot-swap path wrote the raw string straight through
``ModelRegistry.set_load_error``, so the bound ``docs/operations.md``
documents held only until the first rescan replaced the sanitized string
with the raw one — and ``_irspack_compat.py`` front-loads its remedy into a
200-char budget that the steady state was not enforcing.

These tests drive one and the same load failure through both loaders and
assert the property on the entry the registry ends up holding, rather than on
the sanitizer in isolation.  The failure is an ``item_metadata`` fetch that
the SSRF guard refuses: it needs no network, and its message is both longer
than the budget and carries the object URI verbatim.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from recotem.config import ServeConfig
from recotem.serving.app import _try_load_artifact
from recotem.serving.registry import ModelEntry, ModelRegistry, sanitize_load_error
from recotem.serving.watcher import ArtifactWatcher, _RecipeWatchState
from tests.conftest import ACTIVE_KEY_HEX

# A metadata URL whose path segments are the thing redaction exists to keep
# out of a log aggregator.  Loopback with a literal IP so the guard refuses
# on the address alone — no DNS lookup, no socket, no flake.
_SECRET_SEGMENT = "customer-catalog"
_METADATA_URL = f"https://127.0.0.1:9/private/{_SECRET_SEGMENT}/items.csv"

_RECIPE_NAME = "news"


def _news_artifact(make_artifact) -> bytes:
    """A signed artifact whose header names the recipe it is served under.

    ``conftest``'s default header says ``recipe_name: "test"``, which
    ``check_artifact_recipe_name`` refuses for a recipe named "news" —
    before the metadata failure these tests are about is ever reached.
    The header has to agree with the recipe for the load to get that far.
    """
    return make_artifact(
        header_dict={
            "recipe_name": _RECIPE_NAME,
            "trained_at": "2026-01-01T00:00:00Z",
            "best_class": "TopPopRecommender",
        }
    )


@pytest.fixture(autouse=True)
def _enforce_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's blanket ``RECOTEM_HTTP_ALLOW_PRIVATE=1``.

    The documented idiom for exercising the guard (see ``tests/conftest.py``).
    With the opt-in active the fetcher would actually dial 127.0.0.1:9; the
    refusal path is deterministic and offline.
    """
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")


def _recipe_with_remote_metadata(artifact_path: Path) -> Any:
    """A recipe whose item_metadata load fails with a URI-carrying message."""
    return types.SimpleNamespace(
        name=_RECIPE_NAME,
        output=types.SimpleNamespace(path=str(artifact_path)),
        item_metadata=types.SimpleNamespace(
            type="csv",
            path=_METADATA_URL,
            fields=["title"],
            on_field_missing="error",
            item_id_column="item_id",
            sha256="a" * 64,
        ),
    )


def _assert_sanitized(error: str | None) -> None:
    assert error is not None, "the load failure must be recorded"
    assert len(error) <= 200, (
        f"last_load_error must be truncated to 200 chars; got {len(error)}: {error!r}"
    )
    assert "<redacted-uri>" in error, f"URI must be redacted; got {error!r}"
    assert _SECRET_SEGMENT not in error, f"URI path leaked; got {error!r}"


def _serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    return cfg


# ---------------------------------------------------------------------------
# Startup path (serving/app.py) and hot-swap path (serving/watcher.py)
# ---------------------------------------------------------------------------


def test_startup_path_sanitizes_load_error(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(_news_artifact(make_artifact))

    entry, reason = _try_load_artifact(
        _recipe_with_remote_metadata(artifact_path), single_key_ring, _serve_config()
    )

    assert entry.loaded is False, f"metadata failure must not load; reason={reason!r}"
    _assert_sanitized(entry.last_load_error)


def test_watcher_path_sanitizes_load_error(
    tmp_path: Path, make_artifact, single_key_ring
) -> None:
    """The hot-swap loader must hold the same property as startup.

    Uses a real ``ModelEntry`` stub rather than a ``MagicMock``: the
    invariant lives on the entry, and a mock would absorb the write and
    report success no matter what.
    """
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(_news_artifact(make_artifact))

    recipe = _recipe_with_remote_metadata(artifact_path)
    state = _RecipeWatchState(recipe=recipe, artifact_path=str(artifact_path))

    registry = ModelRegistry()
    registry.replace(
        "news",
        ModelEntry(
            name="news",
            recommender=None,
            header={},
            kid="",
            artifact_path=str(artifact_path),
            loaded=False,
        ),
    )

    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=tmp_path,
        serve_config=_serve_config(),
        key_ring=single_key_ring,
        initial_states={"news": state},
    )
    watcher._load_recipe("news", state, force=True)

    entry = registry.get("news")
    assert entry is not None
    _assert_sanitized(entry.last_load_error)


def test_registry_set_load_error_sanitizes() -> None:
    """``set_load_error`` is the watcher's only write door; it must sanitize."""
    registry = ModelRegistry()
    registry.replace(
        "news",
        ModelEntry(name="news", recommender=None, header={}, kid="", loaded=False),
    )

    registry.set_load_error("news", f"read failed: {_METADATA_URL} " + "x" * 300)

    entry = registry.get("news")
    assert entry is not None
    _assert_sanitized(entry.last_load_error)


def test_short_uris_cannot_inflate_past_the_budget() -> None:
    """Redaction must run before truncation, not after.

    ``<redacted-uri>`` is 14 chars and replaces URIs that can be far shorter,
    so substituting into an already-truncated string grows it back past the
    cap: this input surfaced at 408 chars against a documented bound of 200.
    """
    surfaced = sanitize_load_error("read failed: " + "gs://a " * 40)

    assert len(surfaced) <= 200, (
        f"redaction inflated the string past the budget; got {len(surfaced)}"
    )
    assert "<redacted-uri>" in surfaced


def test_watcher_unhealthy_sentinel_survives_sanitization() -> None:
    """The watcher clears its own error by string equality — see W-6.

    ``_mark_all_unhealthy`` writes the sentinel through ``set_load_error``,
    which now rewrites every string on the way in.  If sanitization altered
    the sentinel, ``_clear_unhealthy_markers`` would stop recognising it and
    the recipe would stay degraded forever after a watcher recovery.
    """
    sentinel = ArtifactWatcher._WATCHER_UNHEALTHY_SENTINEL

    assert sanitize_load_error(sentinel) == sentinel

    registry = ModelRegistry()
    registry.replace(
        "news",
        ModelEntry(name="news", recommender=None, header={}, kid="", loaded=False),
    )
    registry.set_load_error("news", sentinel)

    entry = registry.get("news")
    assert entry is not None
    assert entry.last_load_error == sentinel


# ---------------------------------------------------------------------------
# YAML-stub paths — a recipe file that never parsed still gets an entry, and
# its error string reaches /v1/health/details the same way.
# ---------------------------------------------------------------------------


_BROKEN_YAML = "name: broken\nsource: [unclosed\n"


def test_startup_yaml_stub_sanitizes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "broken.yaml").write_text(_BROKEN_YAML)

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.recipes_dir = str(recipes_dir)  # type: ignore[attr-defined]
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    with TestClient(create_app(cfg)) as client:
        details = client.get("/v1/health/details").json()

    # Asserts the bound only, not the wording: a real YAML syntax error already
    # runs to ~400 chars because the parser quotes the absolute path and the
    # offending snippet.  What the message *says* is the recipe loader's
    # business and is deliberately not pinned here.
    error = details["recipes"]["broken"]["error"]
    assert error, "the parse failure must be recorded"
    assert len(error) <= 200, (
        f"YAML-stub error must be truncated to 200 chars; got {len(error)}: {error!r}"
    )


def test_watcher_yaml_stub_sanitizes(tmp_path: Path) -> None:
    """``_register_yaml_failure_stub`` takes the exception, so pass a URI-carrying one."""
    from recotem.recipe.errors import RecipeError

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    yaml_file = recipes_dir / "broken.yaml"
    yaml_file.write_text(_BROKEN_YAML)

    registry = ModelRegistry()
    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=_serve_config(),
        key_ring=None,
        initial_states={},
    )
    watcher._register_yaml_failure_stub(
        yaml_file, RecipeError(f"cannot reach {_METADATA_URL} " + "x" * 300)
    )

    entry = registry.get("broken")
    assert entry is not None
    _assert_sanitized(entry.last_load_error)
