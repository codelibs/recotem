"""A recipe that fails validation must not be reported as a parse error.

``load_recipe`` already separates a YAML *syntax* error (``category="parse"``)
from a schema violation, but serving labelled every rejection "YAML parse",
producing the self-contradictory::

    recipe YAML parse error ... failed validation: - training.n_trials: ...

The file parsed; the schema rejected it.  An operator reading that goes
hunting for a syntax error that does not exist.  These tests pin the wording
on all three sites that surface it — startup, rescan of a known recipe, and
rescan of a brand-new file — and pin that a genuine syntax error still says so.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from recotem.artifact.signing import KeyRing
from recotem.config import ServeConfig
from recotem.recipe.errors import (
    RecipeError,
    describe_recipe_load_failure,
    format_recipe_load_failure,
)
from recotem.recipe.loader import load_recipe
from recotem.serving.registry import ModelRegistry, sanitize_load_error
from recotem.serving.watcher import ArtifactWatcher
from tests.conftest import ACTIVE_KEY_HEX, build_raw_artifact

# A recipe body that parses as YAML but violates the schema (n_trials >= 1).
_SCHEMA_VIOLATION = """\
name: {name}
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 0
output:
  path: {artifact_path}
"""

_VALID = _SCHEMA_VIOLATION.replace("n_trials: 0", "n_trials: 1")

# Unbalanced flow-sequence brackets: a real yaml.YAMLError, not a schema issue.
_SYNTAX_ERROR = ":::invalid yaml:::\nfoo: [unclosed"


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_parse_category_keeps_the_yaml_wording() -> None:
    exc = RecipeError("boom", category="parse")
    assert describe_recipe_load_failure(exc) == "YAML parse failed"


def test_non_parse_categories_do_not_claim_a_parse_error() -> None:
    for category in ("schema", "security", "io", "unknown"):
        phrase = describe_recipe_load_failure(RecipeError("boom", category=category))
        assert "parse" not in phrase.lower(), (
            f"category {category!r} must not be reported as a parse error; "
            f"got {phrase!r}"
        )


def test_non_recipe_exception_does_not_claim_a_parse_error() -> None:
    """The lenient loader also returns duplicate-name and unexpected errors."""
    assert "parse" not in describe_recipe_load_failure(ValueError("boom")).lower()


def test_the_reason_survives_the_health_error_budget(tmp_path: Path) -> None:
    """The 200-char budget must buy the reason, not the directory it sits in.

    ``last_load_error`` is truncated before ``/v1/health/details`` serves it.
    A message that opens with the recipe's absolute path spends that budget
    on a fact the operator already supplied, so how deep the ``--recipes``
    directory happens to sit decided whether the offending field survived the
    cut at all.
    """
    recipes_dir = tmp_path / "srv" / "recotem" / "production" / "eu-west-1"
    recipes_dir.mkdir(parents=True)
    yaml_path = recipes_dir / "demo.yaml"
    yaml_path.write_text(
        _SCHEMA_VIOLATION.format(name="demo", artifact_path=tmp_path / "m.recotem")
    )

    with pytest.raises(RecipeError) as excinfo:
        load_recipe(yaml_path)

    surfaced = sanitize_load_error(
        format_recipe_load_failure(excinfo.value, path=yaml_path, context="on rescan")
    )

    assert "training.n_trials" in surfaced, (
        f"the offending field must survive the 200-char budget; got {surfaced!r}"
    )
    assert str(yaml_path.parent) not in surfaced, (
        f"the directory is redundant with the filename; got {surfaced!r}"
    )
    assert "demo.yaml" in surfaced, f"the file must still be named; got {surfaced!r}"


def test_a_message_that_does_not_name_the_file_still_gets_the_locus() -> None:
    """Not every rejection quotes the file — a scheme refusal names the field.

    Those still need the locus, or an operator watching a directory of
    recipes cannot tell which one to open.
    """
    exc = RecipeError(
        "'source.path' uses scheme 'ftp://' which is not supported for input "
        "paths. Allowed: (none), file, s3, gs",
        category="security",
    )
    surfaced = format_recipe_load_failure(
        exc, path=Path("/srv/recotem/production/eu-west-1/demo.yaml")
    )

    assert "'demo.yaml'" in surfaced, f"the file must be named; got {surfaced!r}"
    assert "/srv/recotem" not in surfaced, f"the directory is dropped; got {surfaced!r}"


# ---------------------------------------------------------------------------
# Startup path (app.py)
# ---------------------------------------------------------------------------


def _serve_details(tmp_path: Path, filename: str, body: str) -> dict:
    """Start serve over a single broken recipe and return /v1/health/details."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / filename).write_text(body)

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    client = TestClient(create_app(cfg))
    return client.get("/v1/health/details").json()


def test_startup_schema_violation_is_not_called_a_parse_error(tmp_path: Path) -> None:
    body = _SCHEMA_VIOLATION.format(name="demo", artifact_path=tmp_path / "m.recotem")
    details = _serve_details(tmp_path, "demo.yaml", body)

    error = details["recipes"]["demo"]["error"]
    assert "failed validation" in error, (
        f"precondition: the file must have failed validation; got {error!r}"
    )
    assert "parse" not in error.lower(), (
        f"a schema violation must not be reported as a parse error; got {error!r}"
    )


def test_startup_syntax_error_is_still_called_a_parse_error(tmp_path: Path) -> None:
    details = _serve_details(tmp_path, "demo.yaml", _SYNTAX_ERROR)
    error = details["recipes"]["demo"]["error"]
    assert "YAML parse failed" in error, (
        f"a genuine syntax error must still say so; got {error!r}"
    )


def test_startup_accounting_for_an_invalid_recipe_is_unchanged(
    tmp_path: Path,
) -> None:
    """Wording is the only thing that changes: a rejected file is still
    ``skipped`` and still excluded from the readiness ``total``."""
    from fastapi.testclient import TestClient

    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "demo.yaml").write_text(
        _SCHEMA_VIOLATION.format(name="demo", artifact_path=tmp_path / "m.recotem")
    )

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "localhost", "127.0.0.1", "*"]

    client = TestClient(create_app(cfg))
    health = client.get("/v1/health").json()

    assert health["total"] == 0, f"a skipped file must not count in total; {health!r}"
    assert health["skipped"] == 1, f"a skipped file must be reported; {health!r}"
    assert client.get("/v1/health/details").json()["recipes"]["demo"]["skipped"] is True


# ---------------------------------------------------------------------------
# Rescan paths (watcher.py)
# ---------------------------------------------------------------------------


def _make_serve_config() -> ServeConfig:
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.watch_interval = 0.05
    cfg.max_artifact_bytes = 100 * 1024 * 1024
    return cfg


def _run_watcher_until(recipes_dir: Path, registry: ModelRegistry, predicate):
    watcher = ArtifactWatcher(
        registry=registry,
        recipes_dir=recipes_dir,
        serve_config=_make_serve_config(),
        key_ring=KeyRing(f"active:{ACTIVE_KEY_HEX}"),
    )
    watcher.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False
    finally:
        watcher.stop()
        watcher.join(timeout=3.0)


def _write_artifact(path: Path, recipe_name: str) -> None:
    import pickle  # noqa: S403  # test fixture: payload built locally

    path.write_bytes(
        build_raw_artifact(
            kid="active",
            key_hex=ACTIVE_KEY_HEX,
            header_dict={
                "recipe_name": recipe_name,
                "best_class": "TopPop",
                "trained_at": "2026-01-01T00:00:00Z",
            },
            payload_bytes=pickle.dumps({"tag": "v1"}, protocol=4),  # noqa: S301
        )
    )


def _error_for(registry: ModelRegistry, name: str) -> str:
    entry = registry.get(name)
    return (entry.last_load_error or "") if entry is not None else ""


def test_rescan_of_a_new_file_names_the_real_failure(tmp_path: Path) -> None:
    """A brand-new file that fails validation gets a stub, worded correctly."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    registry = ModelRegistry()

    def _drop_broken_file() -> bool:
        path = recipes_dir / "demo.yaml"
        if not path.exists():
            path.write_text(
                _SCHEMA_VIOLATION.format(
                    name="demo", artifact_path=tmp_path / "m.recotem"
                )
            )
            return False
        return bool(_error_for(registry, "demo"))

    assert _run_watcher_until(recipes_dir, registry, _drop_broken_file), (
        "watcher must register a stub for the invalid file within 3s"
    )
    error = _error_for(registry, "demo")
    assert "failed validation" in error, f"precondition; got {error!r}"
    assert "parse" not in error.lower(), (
        f"a schema violation must not be reported as a parse error; got {error!r}"
    )


def test_rescan_of_a_new_file_still_reports_a_syntax_error_as_one(
    tmp_path: Path,
) -> None:
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    registry = ModelRegistry()

    def _drop_broken_file() -> bool:
        path = recipes_dir / "demo.yaml"
        if not path.exists():
            path.write_text(_SYNTAX_ERROR)
            return False
        return bool(_error_for(registry, "demo"))

    assert _run_watcher_until(recipes_dir, registry, _drop_broken_file)
    assert "YAML parse failed" in _error_for(registry, "demo")


def test_rescan_of_a_known_recipe_names_the_real_failure(tmp_path: Path) -> None:
    """The M-2 path: an already-loaded recipe whose YAML is edited badly.

    It keeps serving, but the error surfaced on ``/v1/health/details`` must
    say what actually went wrong.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    yaml_path = recipes_dir / "demo.yaml"
    artifact_path = tmp_path / "m.recotem"
    # A loadable artifact, so the poll loop does not overwrite the rescan
    # error with an artifact-missing one.
    _write_artifact(artifact_path, "demo")
    yaml_path.write_text(_VALID.format(name="demo", artifact_path=artifact_path))
    registry = ModelRegistry()

    broken_written = False

    def _break_after_discovery() -> bool:
        nonlocal broken_written
        if not broken_written:
            entry = registry.get("demo")
            if entry is None or not entry.loaded:
                return False
            yaml_path.write_text(
                _SCHEMA_VIOLATION.format(name="demo", artifact_path=artifact_path)
            )
            broken_written = True
            return False
        return "failed validation" in _error_for(registry, "demo")

    assert _run_watcher_until(recipes_dir, registry, _break_after_discovery), (
        f"watcher must surface the rescan failure within 3s; "
        f"got {_error_for(registry, 'demo')!r}"
    )
    error = _error_for(registry, "demo")
    assert "on rescan" in error, f"the rescan context must survive; got {error!r}"
    assert "parse" not in error.lower(), (
        f"a schema violation must not be reported as a parse error; got {error!r}"
    )
