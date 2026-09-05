"""`recotem train` must classify an unauthenticated remote ``output.path``.

A local ``output.path`` the process cannot write to already exits 8: the
per-recipe lock is taken next to the artifact and its permission failure is a
``ConfigError``.  A remote ``output.path`` gets no such lock — remote outputs
lock in a host-local directory — so an unauthenticated bucket used to reach
the end of training and then surface the raw object-store SDK exception: an
unmapped exit 1 with the SDK's frames in the ``train_error`` event, for a
deployment mistake no retry will fix.  The trained model is discarded either
way, so the failure has to name its own cause.

The test drives the real botocore credential-resolution path with every
credential source pointed at nothing, rather than mocking the client, so it
exercises the exception the operator actually sees.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from recotem._exit_codes import _EXIT_CONFIG, _map_exception_to_exit
from recotem.cli import app

pytest.importorskip("s3fs", reason="s3:// output requires the s3fs backend")

_ACTIVE_KEY_HEX = "aa" * 32
_BUCKET_URL = "s3://recotem-integration-test-no-such-bucket/model.recotem"

runner = CliRunner()


def _clear_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every botocore credential source resolve to nothing.

    Covers the env-var, shared-file and instance-metadata providers so the
    chain terminates in ``NoCredentialsError`` instead of picking up whatever
    the developer's or the CI runner's machine happens to have configured.
    """
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent/credentials")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/config")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def _write_interactions(path: Path) -> None:
    """A dataset large enough to train, so the run really reaches the write."""
    lines = ["user_id,item_id"]
    for user in range(20):
        for item in range(10):
            lines.append(f"u{user:02d},i{(user + item) % 12:02d}")
    path.write_text("\n".join(lines) + "\n")


def _write_recipe(recipe_path: Path, *, data_path: Path) -> None:
    recipe_path.write_text(
        f"""\
name: remote-output-test
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
  path: {_BUCKET_URL}
  versioning: always_overwrite
"""
    )


def test_train_to_unauthenticated_bucket_exits_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 8 with a message naming credentials, not exit 1 with a frame dump."""
    _clear_aws_credentials(monkeypatch)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{_ACTIVE_KEY_HEX}")

    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)
    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(recipe_path, data_path=data_path)

    result = runner.invoke(app, ["train", str(recipe_path)])

    assert result.exit_code == _EXIT_CONFIG, (
        f"an unauthenticated remote output.path must exit {_EXIT_CONFIG} like "
        f"its local counterpart; got {result.exit_code}. Output: {result.output}"
    )
    assert "credentials" in result.output.lower(), (
        f"the operator must be told it was a credential failure; got: {result.output}"
    )
    assert '  File "' not in result.output, (
        f"the SDK frame dump must not reach the operator; got: {result.output}"
    )


def test_train_error_event_carries_recotem_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``train_error`` event names the failure instead of ``internal_error``.

    Alerting rules branch on the ``code`` field, and a traceback attached to a
    known deployment mistake is noise, so ``exc_info`` must be off.
    """
    from recotem.recipe.loader import load_recipe
    from recotem.training import pipeline as pipeline_mod

    _clear_aws_credentials(monkeypatch)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{_ACTIVE_KEY_HEX}")

    data_path = tmp_path / "interactions.csv"
    _write_interactions(data_path)
    recipe_path = tmp_path / "recipe.yaml"
    _write_recipe(recipe_path, data_path=data_path)
    recipe = load_recipe(recipe_path)

    spy_logger = MagicMock()
    monkeypatch.setattr(pipeline_mod, "logger", spy_logger)

    from recotem.artifact.signing import KeyRing
    from recotem.training.errors import TrainingError

    with pytest.raises(TrainingError) as excinfo:
        pipeline_mod.run_training(
            recipe,
            key_ring=KeyRing(f"active:{_ACTIVE_KEY_HEX}"),
            signing_key="active",
            no_lock=True,
            quiet=True,
        )

    assert excinfo.value.code == "artifact_write_credentials"

    train_error_calls = [
        call
        for call in spy_logger.error.call_args_list
        if call.args and call.args[0] == "train_error"
    ]
    assert train_error_calls, "train_error must be emitted"
    kwargs = train_error_calls[0].kwargs
    assert kwargs.get("code") == "artifact_write_credentials", (
        f"expected a recotem error code; got {kwargs.get('code')!r}"
    )
    assert kwargs.get("exit_code") == _EXIT_CONFIG
    assert kwargs.get("exc_info") is False, (
        "a known deployment mistake must not attach the SDK traceback"
    )


# ---------------------------------------------------------------------------
# R7-P3 — the destination itself, not only the credential resolution
# ---------------------------------------------------------------------------
#
# Measured against MinIO / fake-gcs-server / Azurite, every one of these
# reached the operator as exit 1 with the SDK's own message, after the model
# had already been trained:
#
#   s3 missing bucket      FileNotFoundError <- botocore NoSuchBucket
#   gs missing bucket      FileNotFoundError
#   az missing container   RuntimeError <- azure.core ResourceNotFoundError
#   s3 rejected key        PermissionError


def _classify(exc: BaseException, path: str):
    from recotem.training.pipeline import _artifact_write_credentials_error

    return _artifact_write_credentials_error(exc, path)


@pytest.mark.parametrize(
    ("exc", "label"),
    [
        (FileNotFoundError("The specified bucket does not exist"), "s3 no bucket"),
        (
            FileNotFoundError("https://storage.googleapis.com/upload/..."),
            "gs no bucket",
        ),
        (
            PermissionError("The request signature we calculated does not match"),
            "s3 403",
        ),
    ],
)
def test_remote_write_destination_failure_is_a_config_error(exc, label) -> None:
    """A remote destination that cannot be written to must exit 8, not 1."""
    err = _classify(exc, "s3://some-bucket/out/model.recotem")
    assert err is not None, f"{label} was left unclassified (exit 1)"
    assert err.code == "artifact_write_destination"
    assert _map_exception_to_exit(err) == _EXIT_CONFIG


def test_remote_write_destination_failure_walks_the_cause_chain() -> None:
    """adlfs wraps the Azure error in a bare RuntimeError — follow the cause."""

    class ResourceNotFoundError(Exception):
        pass

    wrapper = RuntimeError("Failed to upload block: The specified container ...")
    wrapper.__cause__ = ResourceNotFoundError("container not found")
    err = _classify(wrapper, "az://some-container/out/model.recotem")
    assert err is not None, "the Azure missing-container write was unclassified"
    assert err.code == "artifact_write_destination"
    assert _map_exception_to_exit(err) == _EXIT_CONFIG


def test_local_write_failures_keep_their_existing_classification() -> None:
    """Scoped to remote paths: a local path must not be relabelled.

    A local ``output.path`` already answers exit 8 through the per-recipe
    lock's ``LockPermissionError``; re-classifying it here would move where
    that answer comes from for no benefit.
    """
    for path in ("/var/lib/recotem/model.recotem", "file:///tmp/model.recotem"):
        assert _classify(FileNotFoundError("no such file"), path) is None, (
            f"a local path ({path}) must keep its existing classification"
        )


def test_credential_resolution_failures_keep_their_own_code() -> None:
    """The pre-existing credential code must not be swallowed by the new one."""

    class NoCredentialsError(Exception):
        pass

    err = _classify(NoCredentialsError("Unable to locate credentials"), "s3://b/m")
    assert err is not None
    assert err.code == "artifact_write_credentials", (
        "a credential-resolution failure must keep its more specific code"
    )


def test_transient_remote_write_errors_are_still_unclassified() -> None:
    """A 5xx / timeout is not a config error and must keep exiting 1."""
    assert _classify(TimeoutError("read timed out"), "s3://b/m") is None
    assert _classify(ConnectionResetError("reset by peer"), "s3://b/m") is None
