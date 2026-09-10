"""A local ``output.path`` that names a directory must exit 8, not 1.

``_artifact_write_credentials_error`` mapped remote write failures to
``_EXIT_CONFIG`` and stated that a local ``output.path`` could not reach it,
"because ``_write_atomic`` creates missing parents and the lock already refused
an unwritable directory".  ``test_local_write_failures_keep_their_existing_
classification`` in the neighbouring module says the same thing: "a local
``output.path`` already answers exit 8 through the per-recipe lock's
``LockPermissionError``".

One local shape defeats both halves.  When ``output.path`` names an **existing
directory**, ``_write_atomic`` has no parent to create, and the lock is taken at
``<output_path>.lock`` -- a *sibling* of the destination -- so it is created
happily and says nothing about whether the artifact can be written.  Measured on
``08b1672`` through the real CLI:

    output.path names an existing directory   -> exit 1  IsADirectoryError,
                                                 code "internal_error",
                                                 after the full Optuna search
    output.path in a read-only directory      -> exit 8  LockPermissionError
    output.path parent missing                -> exit 0  (recotem creates it)
    output.path plain and writable            -> exit 0

Row 2 is the control that makes row 1 a gap rather than a missing mapping in
general.  Exit 1 is ``_EXIT_UNKNOWN``, "unhandled / unmapped exception", so a
supervisor or CronJob reads a permanently broken recipe as a recotem crash and
retries it -- and the failure lands after fetch, cleansing, split and the whole
search, so on a BigQuery- or SQL-backed recipe the scan is billed first.  This
is the same shape #321 fixed for ``training.storage_path``.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from recotem._exit_codes import _EXIT_CONFIG, _EXIT_SUCCESS, _map_exception_to_exit
from recotem.cli import app

_ACTIVE_KEY_HEX = "aa" * 32

runner = CliRunner()


def _write_interactions(path: Path) -> None:
    """A dataset large enough to train, so the run really reaches the write."""
    lines = ["user_id,item_id"]
    for user in range(20):
        for item in range(10):
            lines.append(f"u{user:02d},i{(user + item) % 12:02d}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_recipe(recipe_path: Path, *, data_path: Path, output: Path) -> None:
    recipe_path.write_text(
        f"""\
name: local-output-test
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
  path: {output.as_posix()}
  versioning: always_overwrite
""",
        encoding="utf-8",
    )


def _train(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Path):
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{_ACTIVE_KEY_HEX}")
    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)
    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(recipe_path, data_path=data_path, output=output)
    return runner.invoke(app, ["train", str(recipe_path)])


# ---------------------------------------------------------------------------
# End to end through the CLI
# ---------------------------------------------------------------------------


def test_output_path_naming_a_directory_exits_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect: exit 8 naming the field, not exit 1 with an errno."""
    output = tmp_path / "model.recotem"
    output.mkdir()

    result = _train(tmp_path, monkeypatch, output)

    assert result.exit_code == _EXIT_CONFIG, (
        f"an output.path naming a directory can never succeed and must exit "
        f"{_EXIT_CONFIG}, like the read-only directory next to it; got "
        f"{result.exit_code}. A supervisor reads exit 1 as a recotem crash "
        f"and retries. Output: {result.output}"
    )
    assert "output.path" in result.output, (
        f"the operator must be told which recipe field is wrong; got: {result.output}"
    )
    assert '  File "' not in result.output, (
        f"the frame dump must not reach the operator; got: {result.output}"
    )


def test_directory_output_train_error_event_carries_a_recotem_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structured event must not say ``internal_error``.

    ``code`` is what a log pipeline branches on, and ``internal_error`` sends
    an operator hunting a recotem bug instead of their own ``output.path``.
    """
    output = tmp_path / "model.recotem"
    output.mkdir()

    result = _train(tmp_path, monkeypatch, output)

    codes = [
        json.loads(line).get("code")
        for line in result.output.splitlines()
        if line.startswith("{") and '"event": "train_error"' in line
    ]
    assert codes, f"no train_error event was emitted; got: {result.output}"
    assert codes[-1] == "artifact_write_destination", (
        f"train_error carried code {codes[-1]!r}; a directory output.path is a "
        "configuration mistake, not an internal error"
    )


def test_read_only_output_directory_keeps_its_existing_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control, and a regression fence.

    This case already exited 8 through ``LockPermissionError``. It proves the
    exit-8 route is live on this rig -- so the test above measures a real gap
    and not a missing mapping in general -- and that the new branch does not
    take the answer over from the lock.
    """
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(0o500)
    try:
        result = _train(tmp_path, monkeypatch, ro_dir / "model.recotem")
    finally:
        ro_dir.chmod(0o700)

    assert result.exit_code == _EXIT_CONFIG, result.output
    codes = [
        json.loads(line).get("code")
        for line in result.output.splitlines()
        if line.startswith("{") and '"event": "train_error"' in line
    ]
    assert codes and codes[-1] == "lock_permission_denied", (
        f"the read-only directory must keep answering through the lock; got {codes}"
    )


def test_writable_output_path_still_trains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the ordinary path is untouched."""
    result = _train(tmp_path, monkeypatch, tmp_path / "out" / "model.recotem")

    assert result.exit_code == _EXIT_SUCCESS, result.output


# ---------------------------------------------------------------------------
# The classifier's boundaries
# ---------------------------------------------------------------------------


def _classify(exc: BaseException, path: str):
    from recotem.training.pipeline import _local_write_destination_error

    return _local_write_destination_error(exc, path)


def test_directory_output_is_classified_as_a_config_error(tmp_path: Path) -> None:
    d = tmp_path / "model.recotem"
    d.mkdir()
    for spelling in (str(d), f"file://{d.as_posix()}"):
        err = _classify(IsADirectoryError(21, "Is a directory"), spelling)
        assert err is not None, f"{spelling} was left unclassified (exit 1)"
        assert err.code == "artifact_write_destination"
        assert _map_exception_to_exit(err) == _EXIT_CONFIG


def test_classifier_is_scoped_to_local_paths(tmp_path: Path) -> None:
    """Mirrors the remote classifier's scope, so neither relabels the other.

    A remote path must fall through to the remote classifier's own rules --
    including its rule that a transient object-store error stays unclassified.
    """
    for remote in ("s3://b/model.recotem", "gs://b/m", "az://c/m", "abfss://c@a/m"):
        assert _classify(IsADirectoryError(21, "Is a directory"), remote) is None, (
            f"a remote path ({remote}) must keep the remote classification"
        )


def test_transient_local_write_errors_are_still_unclassified(tmp_path: Path) -> None:
    """A full disk or an I/O error is not a configuration error.

    ``_EXIT_CONFIG`` means retrying can never succeed. Only the destination
    *being a directory* is checked, so every other local write failure keeps
    exiting 1 and stays retryable.
    """
    target = tmp_path / "model.recotem"
    for exc in (
        OSError(28, "No space left on device"),
        OSError(5, "Input/output error"),
        TimeoutError("stalled mount"),
    ):
        assert _classify(exc, str(target)) is None, (
            f"{exc!r} is not a configuration error and must stay retryable"
        )


def test_non_oserror_write_failures_are_still_unclassified(tmp_path: Path) -> None:
    """A serialisation bug is a real internal error and must keep exit 1."""
    d = tmp_path / "model.recotem"
    d.mkdir()
    assert _classify(TypeError("cannot pickle a socket"), str(d)) is None


def test_a_stat_that_cannot_answer_does_not_escape_the_write_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classifier runs *inside* the artifact write's ``except`` block.

    Its one ``stat`` of ``output.path`` can fail for the same reason the write
    did.  ``Path.is_dir`` swallows only ``ENOENT``/``ENOTDIR``/``EBADF``/
    ``ELOOP`` and re-raises everything else, so on a network filesystem it
    raises rather than answers -- measured on a ``ReadWriteMany`` NFS mount
    whose export changed identity, where it raised ``OSError [Errno 116] Stale
    file handle`` and that replaced the write's own exception.

    Declining is the only safe answer: an unanswerable question is not a "names
    a directory" answer, and every other unclassifiable case here falls through
    to the original error.
    """
    target = tmp_path / "model.recotem"

    def _stale(self: Path) -> bool:
        raise OSError(errno.ESTALE, "Stale file handle")

    monkeypatch.setattr(Path, "is_dir", _stale)

    # Every errno the write itself can carry, including the one this section's
    # own mapping exists for -- none of them may turn into the stat's error.
    for exc in (
        FileExistsError(errno.EEXIST, "File exists"),
        IsADirectoryError(errno.EISDIR, "Is a directory"),
        OSError(errno.EIO, "Input/output error"),
    ):
        assert _classify(exc, str(target)) is None, (
            f"{exc!r} must survive a stat that cannot answer"
        )
