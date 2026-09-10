"""`recotem train` must classify a missing fsspec backend on ``output.path``.

``write_artifact`` resolves ``output.path`` through ``fsspec.core.url_to_fs``,
and fsspec answers an unregistered protocol by importing the backend and
re-raising the failure as ``ImportError`` -- "Please install gcsfs to access
Google Storage" and its s3fs / adlfs equivalents.  Neither
``_artifact_write_credentials_error`` nor ``_local_write_destination_error``
claims an ``ImportError``, so this was the one remote-write failure still
reaching the operator as an unmapped exit 1 with fsspec's frames attached --
after the whole Optuna search had run, and for a ``bigquery`` source after the
scan had been billed.

The backend is made absent through fsspec's own registry rather than by
uninstalling anything, so the test exercises the real ``url_to_fs`` path and
raises the byte-identical message a core install produces.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from recotem._exit_codes import _EXIT_CONFIG, _map_exception_to_exit
from recotem.cli import app
from recotem.training.errors import TrainingError
from recotem.training.pipeline import _artifact_write_driver_error

_ACTIVE_KEY_HEX = "aa" * 32

# (scheme, the message fsspec raises, the recotem extra that fixes it)
_BACKENDS = [
    ("s3", "Install s3fs to access S3", "s3"),
    ("gs", "Please install gcsfs to access Google Storage", "gcs"),
    (
        "az",
        "Install adlfs to access Azure Datalake Gen2 and Azure Blob Storage",
        "azure",
    ),
]

runner = CliRunner()


def _unregister_backend(monkeypatch: pytest.MonkeyPatch, scheme: str, err: str) -> None:
    """Make *scheme* resolve to an absent module, as an uninstalled extra does.

    ``fsspec.registry`` is shadowed on the ``fsspec`` package by the registry
    mapping itself, so the module is reached through ``sys.modules``.
    """
    from fsspec.registry import known_implementations

    registry_module = sys.modules["fsspec.registry"]
    monkeypatch.setitem(
        known_implementations,
        scheme,
        {"class": "recotem_absent_backend.NoFileSystem", "err": err},
    )
    monkeypatch.delitem(registry_module._registry, scheme, raising=False)


def _write_interactions(path: Path) -> None:
    """A dataset large enough to train, so the run really reaches the write."""
    lines = ["user_id,item_id"]
    for user in range(20):
        for item in range(10):
            lines.append(f"u{user:02d},i{(user + item) % 12:02d}")
    path.write_text("\n".join(lines) + "\n")


def _write_recipe(recipe_path: Path, *, data_path: Path, output_path: str) -> None:
    recipe_path.write_text(
        f"""\
name: remote-output-driver-test
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
  path: {output_path}
  versioning: always_overwrite
"""
    )


@pytest.mark.parametrize(("scheme", "err", "extra"), _BACKENDS)
def test_missing_backend_exits_config(
    scheme: str,
    err: str,
    extra: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 8 naming the extra, not exit 1 with fsspec's frames."""
    _unregister_backend(monkeypatch, scheme, err)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{_ACTIVE_KEY_HEX}")

    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)
    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(
        recipe_path,
        data_path=data_path,
        output_path=f"{scheme}://recotem-test-bucket/model.recotem",
    )

    result = runner.invoke(app, ["train", str(recipe_path)])

    assert result.exit_code == _EXIT_CONFIG, (
        f"a missing fsspec backend on output.path must exit {_EXIT_CONFIG} like "
        f"every other configuration failure of this write; got "
        f"{result.exit_code}. Output: {result.output}"
    )
    assert f"recotem[{extra}]" in result.output, (
        f"the operator must be told which extra to install; got: {result.output}"
    )
    assert '  File "' not in result.output, (
        f"fsspec's frame dump must not reach the operator; got: {result.output}"
    )


def test_train_error_event_carries_driver_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structured event names the failure instead of ``internal_error``."""
    from recotem.artifact.signing import KeyRing
    from recotem.recipe.loader import load_recipe
    from recotem.training import pipeline as pipeline_mod

    _unregister_backend(
        monkeypatch, "gs", "Please install gcsfs to access Google Storage"
    )
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{_ACTIVE_KEY_HEX}")

    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)
    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(
        recipe_path,
        data_path=data_path,
        output_path="gs://recotem-test-bucket/model.recotem",
    )
    recipe = load_recipe(recipe_path)

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    with pytest.raises(TrainingError) as excinfo:
        pipeline_mod.run_training(
            recipe,
            key_ring=KeyRing(f"active:{_ACTIVE_KEY_HEX}"),
            signing_key="active",
            no_lock=True,
            quiet=True,
        )

    assert excinfo.value.code == "artifact_write_driver"
    assert _map_exception_to_exit(excinfo.value) == _EXIT_CONFIG

    train_error_calls = [
        call
        for call in spy_logger.error.call_args_list
        if call.args and call.args[0] == "train_error"
    ]
    assert train_error_calls, "train_error must be emitted"
    kwargs = train_error_calls[0].kwargs
    assert kwargs.get("code") == "artifact_write_driver", (
        f"expected a recotem error code; got {kwargs.get('code')!r}"
    )
    assert kwargs.get("exit_code") == _EXIT_CONFIG
    assert kwargs.get("exc_info") is False, (
        "a known deployment mistake must not attach fsspec's traceback"
    )


def test_classifier_ignores_local_output() -> None:
    """A local ``output.path`` keeps its existing classification.

    ``_write_atomic`` creates missing parents and the per-recipe lock has
    already refused an unwritable directory, so a local path cannot reach here
    with an ``ImportError`` -- and must not be relabelled if it somehow does.
    """
    exc = ImportError("Install s3fs to access S3")
    assert _artifact_write_driver_error(exc, "./artifacts/model.recotem") is None
    assert _artifact_write_driver_error(exc, "file:///tmp/model.recotem") is None


def test_classifier_ignores_non_import_failures() -> None:
    """Only ``ImportError`` is claimed; the other classifiers keep their cases."""
    for exc in (
        FileNotFoundError("bucket does not exist"),
        PermissionError("no PutObject"),
        OSError("Forbidden: no storage.objects.create"),
        TimeoutError("transient"),
    ):
        assert _artifact_write_driver_error(exc, "gs://b/model.recotem") is None, (
            f"{type(exc).__name__} must fall through to the existing classifiers"
        )


def test_classifier_walks_the_exception_chain() -> None:
    """fsspec raises ``ImportError`` *from* ``ModuleNotFoundError``."""
    cause = ModuleNotFoundError("No module named 'gcsfs'")
    exc = ImportError("Please install gcsfs to access Google Storage")
    exc.__cause__ = cause
    wrapped = RuntimeError("wrapped by a caller")
    wrapped.__cause__ = exc

    error = _artifact_write_driver_error(wrapped, "gs://b/model.recotem")
    assert error is not None
    assert error.code == "artifact_write_driver"
    assert "recotem[gcs]" in str(error)
