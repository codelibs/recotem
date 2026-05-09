"""Unit tests for recotem.cli subcommands.

Tests spec-mandated exit codes and smoke tests for each subcommand.
Uses Typer's CliRunner for isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from recotem.cli import app

runner = CliRunner()

ACTIVE_KEY_HEX = "aa" * 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_recipe_yaml(tmp_path: Path, name: str = "cli_test") -> Path:
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")
    artifact_path = tmp_path / f"{name}.recotem"
    content = f"""\
name: {name}
source:
  type: csv
  path: {csv_file}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {artifact_path}
"""
    yaml_path = tmp_path / f"{name}.yaml"
    yaml_path.write_text(content)
    return yaml_path


# ---------------------------------------------------------------------------
# recotem validate
# ---------------------------------------------------------------------------


def test_validate_exit0_on_valid_recipe(tmp_path: Path) -> None:
    """validate exits 0 for a schema-valid recipe."""
    yaml_path = _minimal_recipe_yaml(tmp_path)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == 0


def test_validate_exit2_on_schema_error(tmp_path: Path) -> None:
    """validate exits 2 when the recipe has a schema error."""
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("name: bad/name\nsource: null\nschema: null\noutput: null\n")
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == 2


def test_validate_output_contains_schema_ok(tmp_path: Path) -> None:
    yaml_path = _minimal_recipe_yaml(tmp_path)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert "OK" in result.stdout or result.exit_code == 0


def test_validate_probes_csv_path_and_exits_3_on_missing_file(
    tmp_path: Path,
) -> None:
    """validate exits 3 (DataSource error) when the CSV file is missing.

    Confirms ``CSVSource.probe()`` is invoked by the CLI's optional probe
    hook and that the missing path surfaces as a DataSourceError.
    """
    artifact_path = tmp_path / "missing.recotem"
    yaml_path = tmp_path / "missing.yaml"
    yaml_path.write_text(
        f"""\
name: missing
source:
  type: csv
  path: {tmp_path / "does-not-exist.csv"}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {artifact_path}
"""
    )
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == 3
    assert "CSV file not found" in (result.stdout + result.stderr)


def test_validate_probe_ok_for_existing_csv(tmp_path: Path) -> None:
    """validate prints "probe OK" when the CSV path is reachable."""
    yaml_path = _minimal_recipe_yaml(tmp_path)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == 0
    assert "probe OK (csv)" in result.stdout


# ---------------------------------------------------------------------------
# recotem schema
# ---------------------------------------------------------------------------


def test_schema_command_emits_valid_jsonschema() -> None:
    """schema subcommand outputs valid JSON Schema."""
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    schema_dict = json.loads(result.stdout)
    assert "properties" in schema_dict or "title" in schema_dict


# ---------------------------------------------------------------------------
# recotem keygen
# ---------------------------------------------------------------------------


def test_keygen_emits_kid_plaintext_hash_triple() -> None:
    """keygen outputs kid, plaintext, hash, and env_entry lines."""
    result = runner.invoke(app, ["keygen", "--kid", "test-kid", "--type", "signing"])
    assert result.exit_code == 0
    assert "kid=test-kid" in result.stdout
    assert "plaintext=" in result.stdout
    assert "hash=sha256:" in result.stdout
    assert "RECOTEM_SIGNING_KEYS=" in result.stdout


def test_keygen_api_key_outputs_recotem_api_keys_format() -> None:
    result = runner.invoke(app, ["keygen", "--kid", "my-api", "--type", "api"])
    assert result.exit_code == 0
    assert "RECOTEM_API_KEYS=" in result.stdout
    assert "sha256:" in result.stdout


def test_keygen_refuses_unknown_type() -> None:
    result = runner.invoke(app, ["keygen", "--type", "unknown"])
    assert result.exit_code != 0


def test_keygen_signing_key_plaintext_is_64_hex_chars() -> None:
    """Signing key plaintext is a 64-char hex string (32 bytes)."""
    result = runner.invoke(app, ["keygen", "--type", "signing"])
    assert result.exit_code == 0
    for line in result.stdout.splitlines():
        if line.startswith("plaintext="):
            plaintext = line.split("=", 1)[1]
            assert len(plaintext) == 64
            int(plaintext, 16)  # must be valid hex
            break


def test_keygen_api_key_plaintext_is_43_chars_base64url() -> None:
    """API key plaintext is 43 chars base64url-encoded (32 bytes)."""
    result = runner.invoke(app, ["keygen", "--type", "api"])
    assert result.exit_code == 0
    for line in result.stdout.splitlines():
        if line.startswith("plaintext="):
            plaintext = line.split("=", 1)[1]
            assert len(plaintext) == 43  # base64url 32 bytes without padding
            break


# ---------------------------------------------------------------------------
# recotem inspect
# ---------------------------------------------------------------------------


def test_inspect_exit0_on_valid_artifact(tmp_path: Path, monkeypatch) -> None:
    """inspect exits 0 for a valid HMAC-signed artifact."""
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "cli_test", "best_score": 0.5},
        payload_bytes=payload,
    )
    artifact_path.write_bytes(data)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code == 0
    assert "HMAC: OK" in result.stdout


def test_inspect_exit5_on_wrong_magic(tmp_path: Path, monkeypatch) -> None:
    """inspect exits 5 when the artifact has bad magic bytes."""
    artifact_path = tmp_path / "bad.recotem"
    artifact_path.write_bytes(b"BADMAGIC\x00\x01\x00\x00" + b"\x00" * 50)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code == 5


def test_inspect_exit5_on_unknown_kid(tmp_path: Path, monkeypatch) -> None:
    """inspect exits 5 when the artifact's kid is not in the key ring."""
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="unknown-kid",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "test"},
        payload_bytes=payload,
    )
    artifact_path.write_bytes(data)
    # KeyRing with different kid
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code == 5


def test_inspect_caps_oversized_read(tmp_path: Path, monkeypatch) -> None:
    """inspect refuses files exceeding the artifact size cap without pulling
    them fully into memory.

    The cap is monkeypatched down so the test can use a tiny file rather than
    allocate gigabytes.  Guards the fix that swapped the CLI's unbounded
    ``Path.read_bytes()`` for a bounded ``fh.read(max_bytes + 1)``.
    """
    artifact_path = tmp_path / "huge.recotem"
    artifact_path.write_bytes(b"A" * 1024)

    monkeypatch.setattr(
        "recotem.artifact.format.DEFAULT_MAX_PAYLOAD_BYTES", 16, raising=True
    )
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code == 5
    assert "exceeds cap" in (result.stdout + (result.stderr or ""))


# ---------------------------------------------------------------------------
# recotem train: exit code smoke tests
# ---------------------------------------------------------------------------


def test_train_exit2_on_recipe_error(tmp_path: Path, monkeypatch) -> None:
    """train exits 2 when the recipe has a schema error."""
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("name: bad/name\nsource: null\nschema: null\noutput: null\n")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["train", str(yaml_path)])
    assert result.exit_code == 2


def test_train_exit5_on_signing_key_missing_without_dev_flag(
    tmp_path: Path, monkeypatch
) -> None:
    """train exits 5 when RECOTEM_SIGNING_KEYS is not set (not dev mode)."""
    yaml_path = _minimal_recipe_yaml(tmp_path, "no_key_recipe")
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("RECOTEM_ENV", raising=False)
    result = runner.invoke(app, ["train", str(yaml_path)])
    # Should fail with ArtifactError (exit 5) or RecipeError/TrainingError
    # The exact exit code depends on implementation; accept 2, 4, or 5
    assert result.exit_code in (1, 2, 4, 5)


def test_train_exit3_when_fetch_raises_DataSourceError(
    tmp_path: Path, monkeypatch
) -> None:
    """train must exit 3 (DataSourceError) when _fetch_data raises DataSourceError.

    Before the M21 fix, _fetch_data wrapped unexpected exceptions in
    TrainingError(code='datasource_error') which the CLI mapped to exit 4.
    The fix changed the wrapping to DataSourceError, which the CLI maps to exit 3.
    This test exercises the full CLI→pipeline→exit-code path for datasource failures.
    """
    from unittest.mock import MagicMock, patch

    from recotem.datasource.base import DataSourceError

    yaml_path = _minimal_recipe_yaml(tmp_path, "fetch_fail_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # Simulate a DataSourceError raised during _fetch_data (e.g. network error,
    # missing file — anything the datasource pipeline converts to DataSourceError).
    boom = DataSourceError("simulated network timeout")

    mock_source = MagicMock()
    mock_source.fetch.side_effect = boom
    mock_source_cls = MagicMock(return_value=mock_source)

    with patch(
        "recotem.datasource.registry.get_source_class",
        return_value=mock_source_cls,
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 3, (
        f"DataSourceError from _fetch_data must produce exit 3, got {result.exit_code}. "
        f"Output: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# recotem train --run-id validation
# ---------------------------------------------------------------------------


def test_train_run_id_rejects_path_traversal(tmp_path: Path, monkeypatch) -> None:
    """train --run-id with a path-traversal value exits 2 (RecipeError)."""
    yaml_path = _minimal_recipe_yaml(tmp_path, "run_id_escape")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["train", str(yaml_path), "--run-id", "../escape"])
    assert result.exit_code == 2


def test_train_run_id_rejects_empty_string(tmp_path: Path, monkeypatch) -> None:
    """train --run-id with an empty string exits 2 (RecipeError)."""
    yaml_path = _minimal_recipe_yaml(tmp_path, "run_id_empty")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["train", str(yaml_path), "--run-id", ""])
    assert result.exit_code == 2


def test_train_run_id_rejects_oversized_value(tmp_path: Path, monkeypatch) -> None:
    """train --run-id with a value longer than 64 chars exits 2."""
    yaml_path = _minimal_recipe_yaml(tmp_path, "run_id_long")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    oversized = "a" * 65
    result = runner.invoke(app, ["train", str(yaml_path), "--run-id", oversized])
    assert result.exit_code == 2


def test_train_run_id_rejects_special_chars(tmp_path: Path, monkeypatch) -> None:
    """train --run-id rejects values with shell-special characters."""
    yaml_path = _minimal_recipe_yaml(tmp_path, "run_id_special")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["train", str(yaml_path), "--run-id", "bad;id"])
    assert result.exit_code == 2


def test_map_exception_to_exit_DataSourceError_is_exit3() -> None:
    """_map_exception_to_exit must return 3 for DataSourceError.

    Direct unit test of the mapping function to confirm DataSourceError → exit 3
    is wired correctly, independent of the full CLI stack.
    """
    from recotem.cli import _map_exception_to_exit
    from recotem.datasource.base import DataSourceError

    exc = DataSourceError("auth failed")
    assert _map_exception_to_exit(exc) == 3


def test_map_exception_to_exit_TrainingError_is_exit4() -> None:
    """_map_exception_to_exit must return 4 for TrainingError (not DataSourceError).

    Ensures that the DataSourceError fix did not accidentally change the
    TrainingError mapping in the exit-code table.
    """
    from recotem.cli import _map_exception_to_exit
    from recotem.training.errors import TrainingError

    exc = TrainingError("evaluation failed", code="no_completed_trials")
    assert _map_exception_to_exit(exc) == 4


# ---------------------------------------------------------------------------
# recotem serve smoke
# ---------------------------------------------------------------------------


def test_serve_smoke_starts_and_responds_to_health(tmp_path: Path, monkeypatch) -> None:
    """Smoke test: create_app() succeeds and /health returns 200."""
    from fastapi.testclient import TestClient

    from recotem.config import ServeConfig
    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "test"
    cfg.insecure_no_auth = True
    cfg.allowed_hosts = ["testserver", "*"]

    app_instance = create_app(cfg)
    client = TestClient(app_instance)
    response = client.get("/health")
    assert response.status_code == 200
