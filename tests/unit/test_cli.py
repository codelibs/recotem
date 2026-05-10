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


def test_keygen_emits_kid_plaintext_fingerprint_triple() -> None:
    """keygen --type signing outputs kid, plaintext, fingerprint, and env_entry lines."""
    result = runner.invoke(app, ["keygen", "--kid", "test-kid", "--type", "signing"])
    assert result.exit_code == 0
    assert "kid=test-kid" in result.stdout
    assert "plaintext=" in result.stdout
    assert "fingerprint=" in result.stdout
    assert "RECOTEM_SIGNING_KEYS=" in result.stdout
    # Must NOT emit the old misleading "hash=sha256:" line for signing keys
    assert "hash=sha256:" not in result.stdout


def test_keygen_signing_fingerprint_matches_keyring_semantics() -> None:
    """keygen --type signing fingerprint is sha256(key_bytes)[:8] hex."""
    import hashlib

    result = runner.invoke(app, ["keygen", "--kid", "fp-test", "--type", "signing"])
    assert result.exit_code == 0

    plaintext: str | None = None
    fingerprint: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("plaintext="):
            plaintext = line.split("=", 1)[1].strip()
        elif line.startswith("fingerprint="):
            # Strip trailing comment after optional whitespace
            fingerprint = line.split("=", 1)[1].strip().split()[0]

    assert plaintext is not None and fingerprint is not None, result.stdout
    key_bytes = bytes.fromhex(plaintext)
    expected = hashlib.sha256(key_bytes).hexdigest()[:8]
    assert fingerprint == expected, (
        f"fingerprint {fingerprint!r} does not match KeyRing.fingerprint "
        f"semantics sha256(key_bytes)[:8]={expected!r}"
    )


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


def test_inspect_exit5_without_signing_keys(tmp_path: Path, monkeypatch) -> None:
    """inspect exits 5 (non-zero) when RECOTEM_SIGNING_KEYS is unset.

    A scripted pipeline must not receive header output from an unverified
    artifact.  The old 'HMAC: SKIPPED / exit 0' behavior is now an error.
    """
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "no_key_test", "best_score": 0.9},
        payload_bytes=b"dummy",
    )
    artifact_path.write_bytes(data)
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("RECOTEM_ENV", raising=False)
    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code != 0, (
        "inspect must exit non-zero when RECOTEM_SIGNING_KEYS is unset"
    )
    combined = result.stdout + (result.stderr or "")
    assert "RECOTEM_SIGNING_KEYS" in combined


def test_inspect_exit0_dev_allow_unsigned_with_dev_env(
    tmp_path: Path, monkeypatch
) -> None:
    """inspect exits 0 and prints header with --dev-allow-unsigned + RECOTEM_ENV=development."""
    from tests.conftest import build_raw_artifact

    dev_key_hex = "0" * 64  # matches the hardcoded dev key in cli.py
    artifact_path = tmp_path / "model.recotem"
    data = build_raw_artifact(
        kid="dev",
        key_hex=dev_key_hex,
        header_dict={"recipe_name": "dev_test", "best_score": 0.5},
        payload_bytes=b"dummy",
    )
    artifact_path.write_bytes(data)
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("RECOTEM_ENV", "development")
    result = runner.invoke(app, ["inspect", "--dev-allow-unsigned", str(artifact_path)])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "recipe_name" in result.stdout


def test_keygen_api_key_still_prints_hash_sha256() -> None:
    """Regression guard: keygen --type api must still print 'hash=sha256:<hex64>'."""
    result = runner.invoke(app, ["keygen", "--kid", "regression", "--type", "api"])
    assert result.exit_code == 0
    assert "hash=sha256:" in result.stdout
    assert "RECOTEM_API_KEYS=" in result.stdout
    # The fingerprint= line is only for signing keys, not api keys
    assert "fingerprint=" not in result.stdout


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


def test_train_exit8_on_signing_key_missing_without_dev_flag(
    tmp_path: Path, monkeypatch
) -> None:
    """train exits 8 (config error) when RECOTEM_SIGNING_KEYS is not set.

    After E-7 fix: signing_key_missing TrainingError maps to exit 8, not 4 or 5.
    """
    yaml_path = _minimal_recipe_yaml(tmp_path, "no_key_recipe")
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("RECOTEM_ENV", raising=False)
    result = runner.invoke(app, ["train", str(yaml_path)])
    assert result.exit_code == 8, (
        f"Missing RECOTEM_SIGNING_KEYS must produce exit 8, got {result.exit_code}"
    )


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
# C-2: SystemExit collapse fix
# ---------------------------------------------------------------------------


def test_train_systemexit_with_none_code_does_not_collapse_to_zero(
    tmp_path: Path, monkeypatch
) -> None:
    """train must NOT collapse SystemExit(None) to exit 0.

    The old 'exc.code or 0' collapsed SystemExit(None) and other falsy codes
    into a false success.  After the fix, SystemExit(None) must produce a
    non-zero exit.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "se_none")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    def _raises_systemexit_none(*_a, **_kw):
        raise SystemExit(None)

    with patch(
        "recotem.training.pipeline.run_training", side_effect=_raises_systemexit_none
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code != 0, (
        f"SystemExit(None) must produce non-zero exit, got {result.exit_code}"
    )


def test_train_systemexit_with_zero_exits_zero(tmp_path: Path, monkeypatch) -> None:
    """train must exit 0 when run_training raises SystemExit(0).

    Literal int 0 is the only code treated as success.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "se_zero")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    def _raises_systemexit_zero(*_a, **_kw):
        raise SystemExit(0)

    with patch(
        "recotem.training.pipeline.run_training", side_effect=_raises_systemexit_zero
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 0, (
        f"SystemExit(0) must produce exit 0, got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# E-1: serve wraps uvicorn errors
# ---------------------------------------------------------------------------


def test_serve_oserror_returns_dedicated_exit_code(tmp_path: Path, monkeypatch) -> None:
    """serve must catch OSError from uvicorn.run and exit with a non-zero code.

    A bind failure (port in use, permission denied) previously bubbled as an
    unhandled exception.  After the fix it must be caught and map to exit 8
    (configuration error).
    """
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")

    def _raises_oserror(*_a, **_kw):
        raise OSError("address already in use")

    with patch("uvicorn.run", side_effect=_raises_oserror):
        result = runner.invoke(
            app,
            [
                "serve",
                "--recipes",
                str(recipes_dir),
                "--insecure-no-auth",
            ],
        )

    assert result.exit_code == 8, (
        f"OSError from uvicorn.run must map to exit 8 (config), got {result.exit_code}. "
        f"Output: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# E-7: exit code map new buckets
# ---------------------------------------------------------------------------


def test_lock_contested_maps_to_dedicated_exit_code() -> None:
    """_map_exception_to_exit must return 6 for LockContestedError."""
    from recotem.cli import _map_exception_to_exit
    from recotem.training.lock import LockContestedError

    exc = LockContestedError("recipe.yaml locked by pid 42")
    assert _map_exception_to_exit(exc) == 6


def test_http_fetch_error_maps_to_dedicated_exit_code() -> None:
    """_map_exception_to_exit must return 7 for HttpFetchError."""
    from recotem._http_fetch import HttpFetchError
    from recotem.cli import _map_exception_to_exit

    exc = HttpFetchError("SSRF guard: private IP rejected")
    assert _map_exception_to_exit(exc) == 7


def test_missing_signing_keys_maps_to_config_exit_code() -> None:
    """_map_exception_to_exit must return 8 for TrainingError with code='signing_key_missing'.

    When RECOTEM_SIGNING_KEYS is unset run_training raises TrainingError with
    code='signing_key_missing'.  The CLI should map this to exit 8 (config
    error) rather than exit 4 (generic TrainingError).
    """
    from recotem.cli import _map_exception_to_exit
    from recotem.training.errors import TrainingError

    exc = TrainingError("RECOTEM_SIGNING_KEYS is not set.", code="signing_key_missing")
    assert _map_exception_to_exit(exc) == 8


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


# ---------------------------------------------------------------------------
# MAJOR-4: recotem inspect --dev-allow-unsigned must be gated by RECOTEM_ENV
# ---------------------------------------------------------------------------


def test_inspect_dev_allow_unsigned_requires_dev_env(
    tmp_path: Path, monkeypatch
) -> None:
    """``recotem inspect --dev-allow-unsigned`` must refuse to run when
    RECOTEM_ENV is unset (i.e. production).

    Train and serve already gate ``--dev-allow-unsigned`` behind
    ``_check_dev_env`` (RECOTEM_ENV=development), but inspect previously
    let the flag through unconditionally.  An operator who passed the
    flag against a production artifact would silently fall back to a
    deterministic, public dev key, which would still verify because the
    dev key is universally known.
    """
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    # Sign the artifact with the well-known dev key so it would normally
    # verify under --dev-allow-unsigned.
    dev_key_hex = "0" * 64
    data = build_raw_artifact(
        kid="dev",
        key_hex=dev_key_hex,
        header_dict={"recipe_name": "cli_test"},
        payload_bytes=payload,
    )
    artifact_path.write_bytes(data)

    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("RECOTEM_ENV", raising=False)

    result = runner.invoke(app, ["inspect", str(artifact_path), "--dev-allow-unsigned"])
    assert result.exit_code == 2, (
        f"--dev-allow-unsigned must exit with _EXIT_RECIPE (2) when "
        f"RECOTEM_ENV is unset; got {result.exit_code}.  "
        f"Output: {result.output!r}"
    )
    combined = result.output + (result.stderr if result.stderr else "")
    assert "RECOTEM_ENV=development" in combined, (
        f"Error message must mention RECOTEM_ENV requirement.  Got: {combined!r}"
    )


def test_inspect_dev_allow_unsigned_blocked_in_production(
    tmp_path: Path, monkeypatch
) -> None:
    """Same as above, but with RECOTEM_ENV explicitly set to 'production'.

    Any value other than 'development' must trigger the gate.
    """
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="dev",
        key_hex="0" * 64,
        header_dict={"recipe_name": "cli_test"},
        payload_bytes=payload,
    )
    artifact_path.write_bytes(data)

    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("RECOTEM_ENV", "production")

    result = runner.invoke(app, ["inspect", str(artifact_path), "--dev-allow-unsigned"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr if result.stderr else "")
    assert "RECOTEM_ENV=development" in combined


def test_inspect_dev_allow_unsigned_works_in_development(
    tmp_path: Path, monkeypatch
) -> None:
    """When RECOTEM_ENV=development, ``recotem inspect --dev-allow-unsigned``
    must continue to function: an artifact signed with the dev key verifies
    successfully and the header JSON is printed.
    """
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    import pickle  # noqa: S403

    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    data = build_raw_artifact(
        kid="dev",
        key_hex="0" * 64,
        header_dict={"recipe_name": "cli_test"},
        payload_bytes=payload,
    )
    artifact_path.write_bytes(data)

    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("RECOTEM_ENV", "development")

    result = runner.invoke(app, ["inspect", str(artifact_path), "--dev-allow-unsigned"])
    assert result.exit_code == 0, (
        f"--dev-allow-unsigned in development must succeed; output: {result.output!r}"
    )
    assert "HMAC: OK" in result.output


# ---------------------------------------------------------------------------
# MAJOR-11: SSRF via recotem train full CLI flow — DataSourceError (exit 3)
# ---------------------------------------------------------------------------


def test_train_ssrf_private_ip_source_exits_7(tmp_path: Path, monkeypatch) -> None:
    """recotem train with an http source pointing to a private IP must exit 7.

    The SSRF guard fires inside the CSV source's fetch.  ``DataSourceError``
    wraps the underlying ``HttpFetchError`` via ``raise ... from exc``;
    ``_map_exception_to_exit`` walks ``__cause__`` so the canonical exit
    code (7) is preserved for CronJob retry semantics.
    """
    sha256_placeholder = "0" * 64
    recipe_content = f"""\
name: ssrf_test
source:
  type: csv
  path: http://10.0.0.1/data.csv
  sha256: "{sha256_placeholder}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / "out.recotem"}
  versioning: always_overwrite
"""
    yaml_path = tmp_path / "ssrf_recipe.yaml"
    yaml_path.write_text(recipe_content)

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    result = runner.invoke(app, ["train", str(yaml_path)])
    assert result.exit_code == 7, (
        f"Private IP HTTP source must exit 7 (HttpFetchError, even when "
        f"wrapped in DataSourceError); got {result.exit_code}. "
        f"Output: {result.output}"
    )


def test_train_ssrf_loopback_source_exits_7(tmp_path: Path, monkeypatch) -> None:
    """recotem train with a loopback-IP source also exits 7 via __cause__ walk."""
    sha256_placeholder = "0" * 64
    recipe_content = f"""\
name: ssrf_loopback
source:
  type: csv
  path: http://127.0.0.1:1/data.csv
  sha256: "{sha256_placeholder}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / "out.recotem"}
  versioning: always_overwrite
"""
    yaml_path = tmp_path / "ssrf_loop_recipe.yaml"
    yaml_path.write_text(recipe_content)

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_HTTP_ALLOW_PRIVATE", "0")

    result = runner.invoke(app, ["train", str(yaml_path)])
    assert result.exit_code == 7, (
        f"Loopback source must exit 7 (HttpFetchError); got {result.exit_code}"
    )


def test_map_exception_to_exit_walks_cause_chain_for_http_fetch_error() -> None:
    """A DataSourceError wrapping HttpFetchError must map to exit 7, not 3.

    This pins the contract that ``_map_exception_to_exit`` walks the
    ``__cause__`` chain so that CronJob ``restartPolicy: OnFailure`` based on
    exit code (3 = structural data-source failure, 7 = transient network)
    keeps working when datasource layers wrap network errors.
    """
    from recotem._http_fetch import HttpFetchError
    from recotem.cli import _EXIT_HTTP_FETCH, _map_exception_to_exit
    from recotem.datasource.base import DataSourceError

    inner = HttpFetchError("private IP refused")
    try:
        raise DataSourceError("wrapped fetch failure") from inner
    except DataSourceError as exc:
        wrapped = exc

    assert _map_exception_to_exit(wrapped) == _EXIT_HTTP_FETCH


# ---------------------------------------------------------------------------
# MAJOR-6: --dev-allow-unsigned serve - gating in prod env
# ---------------------------------------------------------------------------


def test_serve_dev_allow_unsigned_refused_in_production(
    tmp_path: Path, monkeypatch
) -> None:
    """serve --dev-allow-unsigned must fail when RECOTEM_ENV != 'development'."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "production")

    result = runner.invoke(
        app,
        [
            "serve",
            "--recipes",
            str(recipes_dir),
            "--dev-allow-unsigned",
            "--i-understand-this-loads-arbitrary-code",
            "--insecure-no-auth",
        ],
    )
    assert result.exit_code != 0, (
        f"--dev-allow-unsigned in production must fail; got {result.exit_code}"
    )


def test_serve_dev_allow_unsigned_allowed_in_development_env(
    tmp_path: Path, monkeypatch
) -> None:
    """create_app with dev_allow_unsigned=True in development env must not raise."""
    from fastapi.testclient import TestClient

    from recotem.config import ServeConfig
    from recotem.serving.app import create_app

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_ENV", "development")

    cfg = ServeConfig()
    cfg.signing_keys_raw = f"active:{ACTIVE_KEY_HEX}"
    cfg.recipes_dir = str(recipes_dir)
    cfg.env = "development"
    cfg.insecure_no_auth = True
    cfg.dev_allow_unsigned = True
    cfg.allowed_hosts = ["testserver", "*"]

    app_instance = create_app(cfg)
    client = TestClient(app_instance)
    response = client.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# MAJOR-10: JSON log format end-to-end
# ---------------------------------------------------------------------------


def test_train_json_log_format_stderr_is_valid_json(
    tmp_path: Path, monkeypatch
) -> None:
    """When RECOTEM_LOG_FORMAT=json, recotem train writes valid JSON lines to stderr.

    Tests via subprocess so we can capture real stderr output (CliRunner
    captures stdout/stderr together and does not exercise the real log pipeline).
    We also verify the redaction processor is in effect: the API key value
    must not appear in any log line.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path as _Path

    venv_bin = _Path(sys.executable).parent
    recotem_bin = str(venv_bin / "recotem")

    # Build a recipe that fails at data loading (CSV not found) so we get at
    # least one log line from the pipeline before the error exit.
    missing_csv = tmp_path / "does_not_exist.csv"
    artifact_path = tmp_path / "json_test.recotem"
    yaml_path = tmp_path / "json_log_recipe.yaml"
    yaml_path.write_text(f"""
name: json_log_test
source:
  type: csv
  path: {missing_csv}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {artifact_path}
""")

    api_key_entry = f"kid1:sha256:{'0' * 64}"

    import os

    env = {**os.environ}
    env["RECOTEM_LOG_FORMAT"] = "json"
    env["RECOTEM_SIGNING_KEYS"] = f"active:{ACTIVE_KEY_HEX}"
    env["RECOTEM_API_KEYS"] = api_key_entry
    env.pop("RECOTEM_HTTP_ALLOW_PRIVATE", None)

    result = subprocess.run(
        [recotem_bin, "train", str(yaml_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    # exit code != 0 expected (bad recipe)
    assert result.returncode != 0

    # Structlog JSON lines always start with '{' (JSON objects).
    # Lines that start with other characters are typer.echo() messages (plain text
    # error summaries written by _exit()), not log records — exclude those.
    json_lines = [
        line
        for line in result.stderr.splitlines()
        if line.strip() and line.strip().startswith("{")
    ]
    assert json_lines, (
        f"Expected at least one JSON log line on stderr; got: {result.stderr!r}"
    )

    parse_errors = []
    for line in json_lines:
        try:
            json.loads(line)
        except json.JSONDecodeError as e:
            parse_errors.append(f"Line not valid JSON: {line!r}: {e}")

    assert not parse_errors, "\n".join(parse_errors[:5])

    # The API key hash (hex64) must not appear in any log line
    api_key_hash = "0" * 64  # what we put in RECOTEM_API_KEYS
    for line in json_lines:
        assert api_key_hash not in line, f"API key hash found in log line: {line!r}"


# ---------------------------------------------------------------------------
# CRITICAL: validate probes BigQuery source and surfaces connectivity errors
# ---------------------------------------------------------------------------


def test_validate_probes_bigquery_source_and_surfaces_error(
    tmp_path: Path, monkeypatch
) -> None:
    """recotem validate exits non-zero when BigQuery connectivity fails.

    Recipe with source.type=bigquery and an unreachable project.  The
    datasource registry's get_source_class is mocked to return a stub that
    raises DataSourceError from probe(), simulating an unreachable BigQuery.

    Patching at the datasource-registry level avoids importing
    google.cloud.bigquery for real, which would pollute google.api_core
    module state and break subsequent tests that patch sys.modules.
    """
    from unittest.mock import MagicMock, patch

    from recotem.datasource.base import DataSourceError

    artifact_path = tmp_path / "bq_test.recotem"
    yaml_path = tmp_path / "bq_recipe.yaml"
    yaml_path.write_text(
        f"""\
name: bq_validate_test
source:
  type: bigquery
  query: "SELECT user_id, item_id FROM `my_project.dataset.events` LIMIT 100"
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

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # Build a stub source class whose probe() raises DataSourceError (simulating
    # connectivity failure) and whose __init__ accepts any config without
    # importing google-cloud-bigquery.  This avoids polluting the real
    # google.api_core module state.
    class _StubBQSource:
        extras_required: list = []

        def __init__(self, config) -> None:
            pass

        def probe(self) -> None:
            raise DataSourceError(
                "BigQuery connectivity check failed: project not found in GCP"
            )

    # Return the stub class from get_source_class so the CLI never touches real BQ.
    mock_get_source_class = MagicMock(return_value=_StubBQSource)

    with patch(
        "recotem.datasource.registry.get_source_class",
        mock_get_source_class,
    ):
        result = runner.invoke(app, ["validate", str(yaml_path)])

    assert result.exit_code != 0, (
        f"validate must exit non-zero for unreachable BigQuery source; "
        f"got {result.exit_code}. Output: {result.stdout}"
    )
    combined = result.stdout + (result.stderr or "")
    assert any(
        kw in combined.lower()
        for kw in ("bigquery", "connectivity", "error", "probe", "bq", "failed")
    ), f"Output must mention BigQuery or connectivity error; got: {combined!r}"


# ---------------------------------------------------------------------------
# CRITICAL: inspect exits non-zero on tampered HMAC (payload flip)
# ---------------------------------------------------------------------------


def test_inspect_exit_nonzero_on_tampered_hmac(tmp_path: Path, monkeypatch) -> None:
    """recotem inspect must exit with the artifact error code (5) on HMAC failure.

    A valid artifact is written, then one payload byte is flipped so the
    stored HMAC no longer matches.  inspect must detect the mismatch and
    exit with code 5.
    """
    import pickle  # noqa: S403

    from tests.conftest import build_raw_artifact

    # Build a valid artifact.
    payload = pickle.dumps({"x": 1}, protocol=4)  # noqa: S301
    raw = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "inspect_tamper", "best_score": 0.7},
        payload_bytes=payload,
    )
    # Flip the last byte of the payload to break HMAC.
    tampered = bytearray(raw)
    tampered[-1] ^= 0xFF
    artifact_path = tmp_path / "tampered.recotem"
    artifact_path.write_bytes(bytes(tampered))

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    result = runner.invoke(app, ["inspect", str(artifact_path)])

    assert result.exit_code == 5, (
        f"inspect on a tampered HMAC artifact must exit 5 (ArtifactError); "
        f"got {result.exit_code}. Output: {result.stdout}"
    )
    combined = result.stdout + (result.stderr or "")
    assert any(
        kw in combined.lower() for kw in ("hmac", "signature", "integrity", "tamper")
    ), f"Error output must mention HMAC or signature; got: {combined!r}"


# ---------------------------------------------------------------------------
# CRITICAL: train --run-id propagates to train_done log event
# ---------------------------------------------------------------------------


def test_train_run_id_propagates_to_train_done_log(tmp_path: Path, monkeypatch) -> None:
    """train --run-id <id> must pass the custom id to run_training.

    Mocks run_training to verify the run_id argument equals the value
    passed on the CLI, rather than a freshly-generated UUID.  This
    verifies the CLI→pipeline hand-off without requiring real training data.
    """
    from unittest.mock import MagicMock, patch

    from recotem.training.pipeline import TrainResult

    yaml_path = _minimal_recipe_yaml(tmp_path, "run_id_log_test")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    custom_run_id = "custom-abc-123"

    captured_run_ids: list[str] = []

    fake_result = MagicMock(spec=TrainResult)
    fake_result.recipe_name = "run_id_log_test"
    fake_result.run_id = custom_run_id

    def _mock_run_training(recipe, *, run_id=None, **kwargs):
        captured_run_ids.append(run_id or "")
        return fake_result

    with patch(
        "recotem.training.pipeline.run_training", side_effect=_mock_run_training
    ):
        result = runner.invoke(
            app, ["train", str(yaml_path), "--run-id", custom_run_id]
        )

    assert result.exit_code == 0, (
        f"train with mocked run_training must exit 0; got {result.exit_code}. "
        f"Output: {result.stdout}"
    )
    assert captured_run_ids, "run_training must have been called"
    assert captured_run_ids[0] == custom_run_id, (
        f"run_id passed to run_training must be {custom_run_id!r}; "
        f"got {captured_run_ids[0]!r}"
    )


# ---------------------------------------------------------------------------
# CRITICAL: schema output includes all registered source types
# ---------------------------------------------------------------------------


def test_schema_includes_all_registered_source_types() -> None:
    """recotem schema output must include csv, parquet, and bigquery.

    Parses the JSON schema emitted by the schema command and checks the
    discriminator mapping or the source union definition for the three
    built-in source types.
    """
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0, f"schema must succeed; got: {result.stdout}"

    schema_dict = json.loads(result.stdout)

    # Walk the schema to find all literal 'type' enum values in source
    # definitions.  The exact structure depends on pydantic v2's JSON Schema
    # emission (discriminated union with oneOf / anyOf entries).
    schema_str = json.dumps(schema_dict)

    for expected_type in ("csv", "parquet", "bigquery"):
        assert f'"{expected_type}"' in schema_str, (
            f"Expected source type {expected_type!r} to appear in schema JSON; "
            f"schema excerpt: {schema_str[:500]}"
        )
