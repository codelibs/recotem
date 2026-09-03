"""`recotem train` must not exit 0 when the recipe lock is unwritable.

The lock is acquired before any training work happens.  When the lock path
could not be created, recotem used to treat that exactly like contention:
skip the run and exit 0.  Under cron / a Kubernetes CronJob that is the
worst-shaped failure available — the scheduler records a successful run while
the model silently goes stale, and nothing ever alerts.

A permission failure is a deployment mistake (volume mounted with the wrong
ownership, a read-only filesystem, a mistyped ``RECOTEM_LOCK_DIR``), not a
transient condition, so it must surface as a non-zero exit.  These tests drive
the real CLI against a real read-only directory — no monkeypatching — so they
would catch a regression anywhere between the lock module and the exit-code
mapping.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from recotem._exit_codes import _EXIT_CONFIG, _EXIT_LOCK_CONTESTED, _EXIT_SUCCESS
from recotem.cli import app

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX directory permissions; the Windows lock path is separate",
)

# Deterministic signing key — same format used in tests/conftest.py.
_ACTIVE_KEY_HEX = "aa" * 32


def _write_interactions(path: Path) -> None:
    """A dataset large enough to train, so exit 0 would be a real possibility.

    10 interactions per user against a 12-item catalogue, so a 0.2 holdout
    leaves 2 held-out interactions per user — enough for a non-empty test set.
    """
    lines = ["user_id,item_id"]
    for user in range(20):
        for item in range(10):
            lines.append(f"u{user:02d},i{(user + item) % 12:02d}")
    path.write_text("\n".join(lines) + "\n")


def _write_recipe(recipe_path: Path, *, data_path: Path, output_path: Path) -> None:
    recipe_path.write_text(
        f"""\
name: lock-perm-test
source:
  type: csv
  path: {data_path.as_posix()}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
  cutoff: 3
  split:
    scheme: random
    heldout_ratio: 0.2
    seed: 42
output:
  path: {output_path.as_posix()}
  versioning: always_overwrite
"""
    )


@pytest.mark.parametrize("extra_args", [[], ["--fail-on-busy"]])
def test_train_with_unwritable_lock_dir_does_not_exit_zero(
    tmp_path: Path, extra_args: list[str]
) -> None:
    """The headline guarantee: never a silent success.

    Parametrised over ``--fail-on-busy`` because the old code only raised in
    that mode — the default (and the one every CronJob uses) silently skipped.
    """
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permission checks")

    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)

    # The artifact — and therefore `<output>.lock` — lands in a directory the
    # process may traverse but not write.
    ro_dir = tmp_path / "artifacts_readonly"
    ro_dir.mkdir()
    output_path = ro_dir / "model.recotem"

    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(recipe_path, data_path=data_path, output_path=output_path)

    ro_dir.chmod(0o500)  # r-x: traversable, not writable
    try:
        result = CliRunner().invoke(
            app,
            ["train", str(recipe_path), *extra_args],
            env={**os.environ, "RECOTEM_SIGNING_KEYS": f"active:{_ACTIVE_KEY_HEX}"},
            catch_exceptions=True,
        )
    finally:
        # Restore before assertions so tmp_path cleanup always succeeds.
        ro_dir.chmod(0o700)

    assert result.exit_code != _EXIT_SUCCESS, (
        "recotem train exited 0 with an unwritable lock path — a scheduled run "
        "would report success having trained nothing.\n"
        f"output: {result.output}"
    )
    assert result.exit_code != _EXIT_LOCK_CONTESTED, (
        "a permission failure must not be reported as lock contention "
        f"(exit {_EXIT_LOCK_CONTESTED}), which schedulers read as 'retry later'.\n"
        f"output: {result.output}"
    )
    assert result.exit_code == _EXIT_CONFIG, (
        f"expected exit {_EXIT_CONFIG} (configuration error) but got "
        f"{result.exit_code};\noutput: {result.output}"
    )

    # No artifact may have been produced, and the operator must be told which
    # path failed and what to do about it.
    assert not output_path.exists()
    assert "lock" in result.output.lower()
    assert str(output_path) in result.output or "RECOTEM_LOCK_DIR" in result.output


def test_train_with_writable_lock_dir_still_succeeds(tmp_path: Path) -> None:
    """Control: the same recipe trains fine when the directory is writable.

    Without this, the test above would still pass if `recotem train` were
    broken outright.
    """
    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)

    output_path = tmp_path / "artifacts_writable" / "model.recotem"
    output_path.parent.mkdir()

    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(recipe_path, data_path=data_path, output_path=output_path)

    result = CliRunner().invoke(
        app,
        ["train", str(recipe_path)],
        env={**os.environ, "RECOTEM_SIGNING_KEYS": f"active:{_ACTIVE_KEY_HEX}"},
        catch_exceptions=True,
    )

    assert result.exit_code == _EXIT_SUCCESS, (
        f"control run failed with exit {result.exit_code};\noutput: {result.output}"
    )
    assert output_path.exists()
