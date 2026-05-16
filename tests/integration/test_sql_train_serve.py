"""Integration test: SQLite-backed SQLSource → train → signed artifact.

Covers the contract end-to-end:
  recipe parse → SQLSource.fetch → training → signed artifact written.

Uses run_training directly (matching the existing integration-test convention
from test_serve_predict_e2e.py) rather than the CLI runner, so that the test
can supply a KeyRing object in-process and avoid needing env-var juggling for
the training pipeline itself.

A separate CLI-invocation test covers the exit-code contract (DataSourceError
→ exit 3) when the required DSN env var is absent.

NOTE: unpickle_payload uses the project's SafeUnpickler with a hand-enumerated
FQCN allow-list. Pickle is required here because irspack's IDMappedRecommender
carries scipy sparse matrices and numpy arrays. The existing conftest.py has the
same note.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

# Deterministic signing key — same format used in tests/conftest.py.
_ACTIVE_KEY_HEX = "aa" * 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_db(path: Path) -> None:
    """Create a tiny SQLite events table with 20 users × 10 items = 200 rows."""
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE events (user_id TEXT, item_id TEXT, ts TEXT);")
    rows = [(f"u{u}", f"i{i}", "2026-01-01") for u in range(20) for i in range(10)]
    con.executemany("INSERT INTO events VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()


def _make_sql_recipe(tmp_path: Path, artifact_path: str) -> object:
    """Build an in-memory Recipe object that uses SQLSource (sqlite dialect)."""
    from recotem.datasource.sql import SQLConfig
    from recotem.recipe.models import (
        OutputConfig,
        Recipe,
        SchemaConfig,
        SplitConfig,
        TrainingConfig,
    )

    return Recipe(
        name="sql-test",
        source=SQLConfig(
            type="sql",
            dsn_env="RECOTEM_RECIPE_DB_DSN",
            query="SELECT user_id, item_id FROM events",
        ),
        schema=SchemaConfig(user_column="user_id", item_column="item_id"),
        training=TrainingConfig(
            algorithms=["TopPop"],
            n_trials=1,
            cutoff=5,  # must be < n_items (10) to avoid irspack ValueError
            split=SplitConfig(scheme="random", heldout_ratio=0.2, seed=0),
        ),
        output=OutputConfig(path=artifact_path, versioning="always_overwrite"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sql_train_writes_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: SQLite DSN → fetch → train → .recotem artifact on disk."""
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.training._compat import IDMappedRecommender
    from recotem.training.pipeline import run_training

    db = tmp_path / "events.db"
    _seed_db(db)

    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", f"sqlite:///{db.as_posix()}")

    artifact_path = str(tmp_path / "sql-test.recotem")
    recipe = _make_sql_recipe(tmp_path, artifact_path)

    kr = KeyRing(f"active:{_ACTIVE_KEY_HEX}")
    result = run_training(
        recipe,
        key_ring=kr,
        signing_key="active",
        no_lock=True,
        dev_allow_unsigned=False,
        quiet=True,
    )

    assert result is not None, "run_training returned None unexpectedly"
    assert result.best_class is not None

    # The artifact file must exist on disk.
    written = Path(result.artifact_path)
    assert written.exists(), f"artifact not found at {written}"
    assert written.suffix == ".recotem"

    # Read back and deserialize to confirm the binary format is intact.
    # unpickle_payload uses SafeUnpickler with the project's FQCN allow-list.
    header, payload_bytes = read_artifact(str(written), kr)
    recommender = unpickle_payload(payload_bytes)  # noqa: S301
    assert recommender is not None
    assert isinstance(recommender, IDMappedRecommender)
    assert len(recommender.user_ids) > 0
    assert len(recommender.item_ids) > 0


def test_sql_missing_dsn_env_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DataSourceError (missing DSN env var) → CLI exit code 3."""
    from typer.testing import CliRunner

    from recotem.cli import app

    db = tmp_path / "events.db"
    _seed_db(db)

    # Write recipe YAML so the CLI can load it.
    recipe_yaml = tmp_path / "sql-test.yaml"
    artifact_path = tmp_path / "sql-test.recotem"
    recipe_yaml.write_text(
        f"""\
name: sql-test
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DB_DSN
  query: SELECT user_id, item_id FROM events
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {artifact_path.as_posix()}
"""
    )

    # Ensure the DSN env var is absent and signing keys are present.
    monkeypatch.delenv("RECOTEM_RECIPE_DB_DSN", raising=False)

    env = {
        **os.environ,
        "RECOTEM_SIGNING_KEYS": f"active:{_ACTIVE_KEY_HEX}",
    }
    # Remove the DSN var from the merged env dict too (monkeypatch only affects
    # the current process; CliRunner may snapshot os.environ at call time).
    env.pop("RECOTEM_RECIPE_DB_DSN", None)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["train", str(recipe_yaml), "--no-lock"],
        env=env,
        catch_exceptions=True,
    )

    assert result.exit_code == 3, (
        f"expected exit 3 (DataSourceError) but got {result.exit_code};\n"
        f"output: {result.output}"
    )
