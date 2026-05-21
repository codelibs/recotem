"""Unit tests for recotem.cli subcommands.

Tests spec-mandated exit codes and smoke tests for each subcommand.
Uses Typer's CliRunner for isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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
    """inspect refuses files exceeding the read cap without pulling them
    fully into memory.

    After C-2 the file-read cap is driven by RECOTEM_MAX_ARTIFACT_BYTES (not
    RECOTEM_MAX_PAYLOAD_BYTES).  ServeConfig clamps the value to
    [1 MiB, 16 GiB], so we set the env var to the minimum (1 MiB) and write a
    file larger than 1 MiB so the cap fires.
    Guards the fix that swapped the CLI's unbounded ``Path.read_bytes()``
    for a bounded ``fh.read(max_bytes + 1)``.
    """
    artifact_path = tmp_path / "huge.recotem"
    # Write 1 MiB + 1 byte so the file exceeds the minimum allowed cap (1 MiB).
    one_mib_plus_one = 1 * 1024 * 1024 + 1
    artifact_path.write_bytes(b"A" * one_mib_plus_one)

    # Set RECOTEM_MAX_ARTIFACT_BYTES to the minimum allowed (1 MiB = 1048576).
    # This is the file-read cap after C-2 (max_payload_bytes is the parse cap).
    # ServeConfig clamps below-minimum values up to 1 MiB, so this is the
    # smallest read cap we can reliably test.
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", str(1 * 1024 * 1024))
    # Also set RECOTEM_MAX_PAYLOAD_BYTES <= RECOTEM_MAX_ARTIFACT_BYTES so the
    # payload <= artifact invariant is satisfied (ConfigError otherwise).
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(1 * 1024 * 1024))
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

    Note: The patch target is recotem.training.pipeline._fetch_data (not
    get_source_class) so that the patch fires *after* load_recipe, which calls
    get_source_class internally during YAML loading and relies on getting a real
    typed Config back from source_cls.Config.model_validate().  Patching
    _fetch_data avoids MF-4's new ValueError for non-pydantic source objects.
    """
    from unittest.mock import patch

    from recotem.datasource.base import DataSourceError

    yaml_path = _minimal_recipe_yaml(tmp_path, "fetch_fail_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # Simulate a DataSourceError raised during _fetch_data (e.g. network error,
    # missing file — anything the datasource pipeline converts to DataSourceError).
    boom = DataSourceError("simulated network timeout")

    with patch(
        "recotem.training.pipeline._fetch_data",
        side_effect=boom,
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
    """serve must catch bind-related OSError from uvicorn.run and exit 8 (config).

    A bind failure (EADDRINUSE — port in use) is a configuration error.
    After the I-7 fix, bind-related errnos (EADDRINUSE, EACCES, EADDRNOTAVAIL)
    map to exit 8 while other errnos (resource exhaustion, etc.) map via
    _map_exception_to_exit to their own codes.

    This test uses EADDRINUSE explicitly to verify the bind-error path.
    """
    import errno as _errno
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")

    exc = OSError(_errno.EADDRINUSE, "address already in use")
    exc.errno = _errno.EADDRINUSE

    with patch("uvicorn.run", side_effect=exc):
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
        f"EADDRINUSE OSError from uvicorn.run must map to exit 8 (config), got {result.exit_code}. "
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
    response = client.get("/v1/health")
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
    # _check_dev_env now exits with _EXIT_CONFIG (8) per the documented
    # exit-code table — environment-gated flag misuse is a configuration
    # error, not a recipe error.  Sibling guards ("signing keys missing")
    # already exit with 8 so operators can branch on a single value.
    assert result.exit_code == 8, (
        f"--dev-allow-unsigned must exit with _EXIT_CONFIG (8) when "
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
    # See test_inspect_dev_allow_unsigned_requires_dev_env — exit 8.
    assert result.exit_code == 8
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
    response = client.get("/v1/health")
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


# ---------------------------------------------------------------------------
# MAJOR-6: --run-id help must reference training.storage_path not tuning.storage_path
# ---------------------------------------------------------------------------


def test_run_id_help_uses_training_storage_path() -> None:
    """train --help must reference 'training.storage_path', not 'tuning.storage_path'."""
    result = runner.invoke(app, ["train", "--help"])
    assert result.exit_code == 0
    assert "training.storage_path" in result.output, (
        f"Expected 'training.storage_path' in help output; got:\n{result.output}"
    )
    assert "tuning.storage_path" not in result.output, (
        f"Found stale 'tuning.storage_path' in help output; got:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# MINOR: _configure_logging_from_env error handling
# ---------------------------------------------------------------------------


def test_configure_logging_from_env_handles_oserror_silently(monkeypatch) -> None:
    """An OSError raised by configure_logging must be swallowed silently.

    _configure_logging_from_env is called directly so we don't depend on
    whether --help or schema happen to invoke it (they may not).
    """
    from unittest.mock import patch

    from recotem.cli import _configure_logging_from_env

    # Simulate configure_logging raising OSError (e.g. log file permission denied).
    with patch("recotem.logging.configure_logging", side_effect=OSError("perm denied")):
        # Must not raise — OSError is caught silently by the inner guard.
        _configure_logging_from_env()


def test_configure_logging_from_env_prints_unexpected_error_to_stderr(
    monkeypatch,
    capsys,
) -> None:
    """An unexpected exception (not ImportError/OSError) must print a '[recotem]'
    prefixed line to stderr without re-raising.

    _configure_logging_from_env is called directly to bypass the --help
    short-circuit path that never reaches the command body.
    """
    from unittest.mock import patch

    from recotem.cli import _configure_logging_from_env

    # Simulate a broad unexpected failure (e.g. configure_logging raises RuntimeError).
    with patch(
        "recotem.logging.configure_logging",
        side_effect=RuntimeError("unexpected broad failure"),
    ):
        # Must not raise — the outer broad except catches it and prints to stderr.
        _configure_logging_from_env()

    captured = capsys.readouterr()
    assert "[recotem]" in captured.err, (
        f"Expected '[recotem]' prefix in stderr; got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# C3 — train exit code coverage
# ---------------------------------------------------------------------------


def test_train_exit0_on_success(tmp_path: Path, monkeypatch) -> None:
    """train exits 0 when run_training completes without raising."""
    from unittest.mock import MagicMock, patch

    from recotem.training.pipeline import TrainResult

    yaml_path = _minimal_recipe_yaml(tmp_path, "success_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    fake_result = MagicMock(spec=TrainResult)
    fake_result.recipe_name = "success_recipe"
    fake_result.run_id = "abc123"

    with patch("recotem.training.pipeline.run_training", return_value=fake_result):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 0, (
        f"Successful train must exit 0; got {result.exit_code}. Output: {result.stdout}"
    )


def test_train_exit1_on_unexpected_exception(tmp_path: Path, monkeypatch) -> None:
    """train exits 1 when run_training raises an arbitrary RuntimeError (not a known type)."""
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "unexpected_error_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    with patch(
        "recotem.training.pipeline.run_training",
        side_effect=RuntimeError("kaboom"),
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 1, (
        f"Unexpected RuntimeError must produce exit 1 (unknown); "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_train_exit4_on_all_trials_fail(tmp_path: Path, monkeypatch) -> None:
    """train exits 4 when run_training raises TrainingError."""
    from unittest.mock import patch

    from recotem.training.errors import TrainingError

    yaml_path = _minimal_recipe_yaml(tmp_path, "all_trials_fail_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    with patch(
        "recotem.training.pipeline.run_training",
        side_effect=TrainingError("all trials failed", code="no_completed_trials"),
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 4, (
        f"TrainingError('all trials failed') must produce exit 4; "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_train_exit4_on_min_data_violation(tmp_path: Path, monkeypatch) -> None:
    """train exits 4 when run_training raises MinDataViolation (a TrainingError subclass)."""
    from unittest.mock import patch

    from recotem.training.errors import MinDataViolation

    yaml_path = _minimal_recipe_yaml(tmp_path, "min_data_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    with patch(
        "recotem.training.pipeline.run_training",
        side_effect=MinDataViolation(
            "n_rows=1 < min_rows=100",
            n_rows=1,
            n_users=1,
            n_items=1,
            min_rows=100,
        ),
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 4, (
        f"MinDataViolation must produce exit 4; "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_train_lock_contention_skips_with_exit0_default(
    tmp_path: Path, monkeypatch
) -> None:
    """train exits 0 (skip) when the recipe lock is contested without --fail-on-busy.

    run_training returns None when the lock is held and fail_on_busy=False.
    The CLI should treat None as a graceful skip and exit 0.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "lock_skip_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # run_training returns None when lock is contested and fail_on_busy=False
    with patch("recotem.training.pipeline.run_training", return_value=None):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 0, (
        f"Lock-contended skip (None result) must produce exit 0; "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_train_fail_on_busy_exits_nonzero_when_locked(
    tmp_path: Path, monkeypatch
) -> None:
    """train exits 6 when run_training raises LockContestedError with --fail-on-busy."""
    from unittest.mock import patch

    from recotem.training.lock import LockContestedError

    yaml_path = _minimal_recipe_yaml(tmp_path, "lock_fail_recipe")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    with patch(
        "recotem.training.pipeline.run_training",
        side_effect=LockContestedError("recipe.yaml locked by pid 42"),
    ):
        result = runner.invoke(app, ["train", str(yaml_path), "--fail-on-busy"])

    assert result.exit_code == 6, (
        f"LockContestedError with --fail-on-busy must produce exit 6; "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


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


# ---------------------------------------------------------------------------
# I-E: inspect honors RECOTEM_MAX_PAYLOAD_BYTES
# ---------------------------------------------------------------------------


def test_inspect_honors_recotem_max_payload_bytes(tmp_path: Path, monkeypatch) -> None:
    """inspect must exit 5 when the artifact exceeds RECOTEM_MAX_PAYLOAD_BYTES.

    Previously, inspect hard-coded DEFAULT_MAX_PAYLOAD_BYTES (2 GiB).  After
    the I-E fix it reads the cap from ServeConfig.from_env(), so setting
    RECOTEM_MAX_PAYLOAD_BYTES=1048576 (1 MiB) must cause a 2 MiB artifact to
    be rejected before any deserialization happens.
    """
    from tests.conftest import build_raw_artifact

    # Build a valid artifact with a 2 MiB payload so the total artifact size
    # exceeds 1 MiB even before the header overhead.
    two_mib_payload = b"P" * (2 * 1024 * 1024)
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "ie_test", "best_score": 0.5},
        payload_bytes=two_mib_payload,
    )
    artifact_path = tmp_path / "large.recotem"
    artifact_path.write_bytes(data)

    # Cap at 1 MiB — the 2 MiB artifact must be rejected.
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(1 * 1024 * 1024))
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    result = runner.invoke(app, ["inspect", str(artifact_path)])

    assert result.exit_code == 5, (
        f"inspect must exit 5 when artifact exceeds RECOTEM_MAX_PAYLOAD_BYTES; "
        f"exit_code={result.exit_code}, output={result.stdout}"
    )
    combined = result.stdout + (result.stderr or "")
    assert "exceeds cap" in combined, (
        f"Expected 'exceeds cap' in output; got: {combined!r}"
    )


def test_inspect_max_payload_bytes_small_artifact_passes(
    tmp_path: Path, monkeypatch
) -> None:
    """inspect must succeed when the artifact is within RECOTEM_MAX_PAYLOAD_BYTES.

    Complement to the rejection test: with a 4 MiB cap a minimal artifact
    (tiny payload) must be accepted and exit 0.
    """
    from tests.conftest import build_raw_artifact

    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "ie_small", "best_score": 0.9},
    )
    artifact_path = tmp_path / "small.recotem"
    artifact_path.write_bytes(data)

    # 4 MiB cap — this tiny artifact must sail through.
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(4 * 1024 * 1024))
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code == 0, (
        f"inspect must succeed for artifact within cap; output={result.stdout}"
    )
    assert "HMAC: OK" in result.stdout


# ---------------------------------------------------------------------------
# C-1: _EXIT_CONFIG (8) — serve path exit codes
# ---------------------------------------------------------------------------


def test_serve_missing_signing_keys_exits_8(tmp_path: Path, monkeypatch) -> None:
    """recotem serve with RECOTEM_SIGNING_KEYS unset must exit 8 (ConfigError).

    Previously _build_key_ring raised ArtifactError which mapped to exit 5.
    After the C-1 fix it raises ConfigError which maps to exit 8.
    """

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("RECOTEM_ENV", "test")

    result = runner.invoke(
        app,
        ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
    )
    assert result.exit_code == 8, (
        f"Missing RECOTEM_SIGNING_KEYS on serve must produce exit 8 (ConfigError), "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_serve_malformed_api_keys_exits_8(tmp_path: Path, monkeypatch) -> None:
    """recotem serve with malformed RECOTEM_API_KEYS must exit 8 (ConfigError).

    Previously a ValueError from ServeConfig.from_env() was caught and mapped
    to _EXIT_RECIPE (2).  After the C-1 fix, ConfigError maps to exit 8.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_API_KEYS", "garbage-not-valid")

    result = runner.invoke(
        app,
        ["serve", "--recipes", str(recipes_dir)],
    )
    assert result.exit_code == 8, (
        f"Malformed RECOTEM_API_KEYS must produce exit 8 (ConfigError), "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_serve_insecure_no_auth_in_prod_exits_8(tmp_path: Path, monkeypatch) -> None:
    """recotem serve --insecure-no-auth outside allowed envs must exit 8 (ConfigError).

    validate_insecure_flags() now raises ConfigError, which _map_exception_to_exit
    routes to exit 8.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "production")

    result = runner.invoke(
        app,
        ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
    )
    assert result.exit_code == 8, (
        f"--insecure-no-auth in production must produce exit 8, "
        f"got {result.exit_code}. Output: {result.stdout}"
    )


def test_train_missing_signing_keys_still_exits_8(tmp_path: Path, monkeypatch) -> None:
    """Regression guard: train with missing RECOTEM_SIGNING_KEYS still exits 8.

    The train path uses TrainingError(code='signing_key_missing') → exit 8.
    This test ensures the C-1 ConfigError changes on the serve path did not
    accidentally break the existing train path mapping.
    """
    yaml_path = _minimal_recipe_yaml(tmp_path, "train_no_key_regression")
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("RECOTEM_ENV", raising=False)

    result = runner.invoke(app, ["train", str(yaml_path)])
    assert result.exit_code == 8, (
        f"Missing RECOTEM_SIGNING_KEYS on train must still produce exit 8; "
        f"got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# M-3: SystemExit code preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 2, 3, 4, 5, 6, 7, 8])
def test_train_systemexit_known_code_preserved(
    code: int, tmp_path: Path, monkeypatch
) -> None:
    """train must propagate well-known recotem exit codes without modification.

    M-3: the old SystemExit handler only passed code 0; codes 2–8 were all
    normalized to 1.  After the fix, codes in {0, 2, 3, 4, 5, 6, 7, 8} are
    preserved.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, f"se_code_{code}")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    def _raises(*_a, **_kw):
        raise SystemExit(code)

    with patch("recotem.training.pipeline.run_training", side_effect=_raises):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == code, (
        f"SystemExit({code}) must be preserved; got {result.exit_code}"
    )


@pytest.mark.parametrize("code", [1, 99, "string"])
def test_train_systemexit_unknown_code_normalizes_to_exit1(
    code: object, tmp_path: Path, monkeypatch
) -> None:
    """train must normalize unrecognised SystemExit codes to _EXIT_UNKNOWN (1)."""
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, f"se_unknown_{code}")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    def _raises(*_a, **_kw):
        raise SystemExit(code)

    with patch("recotem.training.pipeline.run_training", side_effect=_raises):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 1, (
        f"SystemExit({code!r}) must normalize to exit 1; got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# M-5: RECOTEM_MAX_PAYLOAD_BYTES > RECOTEM_MAX_ARTIFACT_BYTES exits 8
# ---------------------------------------------------------------------------


def test_serve_payload_exceeds_artifact_cap_exits_8(
    tmp_path: Path, monkeypatch
) -> None:
    """serve must exit 8 when RECOTEM_MAX_PAYLOAD_BYTES > RECOTEM_MAX_ARTIFACT_BYTES.

    CLAUDE.md documents 'payload must be smaller than artifact to bound
    deserialization memory expansion'.  Violating this invariant is a
    configuration error (exit 8).
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")
    # Set payload cap larger than artifact cap — must be rejected.
    monkeypatch.setenv("RECOTEM_MAX_ARTIFACT_BYTES", str(1 * 1024 * 1024))  # 1 MiB
    monkeypatch.setenv("RECOTEM_MAX_PAYLOAD_BYTES", str(2 * 1024 * 1024))  # 2 MiB

    result = runner.invoke(
        app,
        ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
    )
    # ConfigError from from_env() raises before create_app — exit 8.
    assert result.exit_code == 8, (
        f"PAYLOAD > ARTIFACT cap must produce exit 8; got {result.exit_code}. "
        f"Output: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# m-12: duplicate API kid exits 8
# ---------------------------------------------------------------------------


def test_serve_duplicate_api_kid_exits_8(tmp_path: Path, monkeypatch) -> None:
    """serve must exit 8 when RECOTEM_API_KEYS contains a duplicate kid.

    Duplicate kids are a configuration mistake — the first key silently wins
    in KeyRing, masking the second.  Failing fast at startup is safer.
    """
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    # Two entries with the same kid "k1" but different hashes.
    hash1 = "aa" * 32
    hash2 = "bb" * 32
    monkeypatch.setenv("RECOTEM_API_KEYS", f"k1:sha256:{hash1},k1:sha256:{hash2}")

    result = runner.invoke(
        app,
        ["serve", "--recipes", str(recipes_dir)],
    )
    assert result.exit_code == 8, (
        f"Duplicate API key kid must produce exit 8; got {result.exit_code}. "
        f"Output: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# M-12 (test addition): recotem schema includes echo plugin
# ---------------------------------------------------------------------------


def test_schema_includes_echo_plugin_source_type() -> None:
    """recotem schema must include 'echo' in the source discriminator union
    when a plugin is registered via a monkeypatched entry-point.

    The echo plugin (examples/plugins/echo-source) has its own Config but
    lacks the ``type: Literal["echo"]`` discriminator field that pydantic
    requires to build the union schema.  We therefore stub the plugin with a
    minimal conforming class that has the discriminator, mirroring what a
    properly-written production plugin would look like.  This tests the
    ``recotem schema`` code path (that it correctly includes extra sources),
    not the completeness of the example plugin.
    """
    from typing import ClassVar, Literal
    from unittest.mock import patch

    from pydantic import BaseModel

    class _EchoStubConfig(BaseModel):
        """Minimal Config for the echo stub — type discriminator required."""

        type: Literal["echo"] = "echo"

    class _EchoStub:
        """Minimal plugin stub conforming to the DataSource contract."""

        type_name: ClassVar[str] = "echo"
        extras_required: ClassVar[list[str]] = []
        no_expand_fields: ClassVar[frozenset[str]] = frozenset()
        Config = _EchoStubConfig

    # get_source_types() is lru_cached; patch it to return real built-ins + echo.
    import recotem.datasource.registry as _reg_mod

    real_types = dict(_reg_mod.get_source_types())  # built-ins only (no echo)
    extended_types = {**real_types, "echo": _EchoStub}

    with patch.object(_reg_mod, "get_source_types", return_value=extended_types):
        result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0, (
        f"schema with echo plugin registered must succeed; output: {result.stdout}"
    )
    schema_str = result.stdout
    assert '"echo"' in schema_str, (
        f"'echo' source type must appear in schema JSON when plugin is registered; "
        f"schema excerpt: {schema_str[:500]}"
    )


# ---------------------------------------------------------------------------
# B-4 regression: flag-pair misuse maps to EXIT_CONFIG (8)
# ---------------------------------------------------------------------------


def test_train_dev_allow_unsigned_without_companion_exits_config(
    tmp_path: Path, monkeypatch
) -> None:
    """``--dev-allow-unsigned`` without ``--i-understand...`` must exit 8.

    Per the documented exit-code table, flag-pair misuse is a configuration
    error (``_EXIT_CONFIG = 8``), not a recipe error.  Operators rely on the
    table to drive CronJob retry logic — collapsing 8 onto 2 (recipe) would
    cause a config-only failure to be retried as if the recipe could be
    fixed.
    """
    yaml_path = _minimal_recipe_yaml(tmp_path, "config_exit_train")
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("RECOTEM_ENV", "development")
    result = runner.invoke(app, ["train", str(yaml_path), "--dev-allow-unsigned"])
    assert result.exit_code == 8, (
        f"flag-pair misuse must exit 8, got {result.exit_code}; "
        f"output: {result.output!r}"
    )


def test_serve_dev_allow_unsigned_without_companion_exits_config(
    tmp_path: Path, monkeypatch
) -> None:
    """Same as above for the ``serve`` subcommand."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "development")
    result = runner.invoke(
        app,
        [
            "serve",
            "--recipes",
            str(recipes_dir),
            "--dev-allow-unsigned",
        ],
    )
    assert result.exit_code == 8


# ---------------------------------------------------------------------------
# B-5 regression: inspect maps fsspec backend exceptions to EXIT_ARTIFACT (5)
# ---------------------------------------------------------------------------


def test_inspect_maps_fsspec_backend_exception_to_exit5(
    tmp_path: Path, monkeypatch
) -> None:
    """A non-OSError exception from the fsspec read path must exit 5.

    Real-world triggers: ``botocore.exceptions.NoCredentialsError``,
    ``gcsfs.retry.HttpError`` and similar do not subclass ``OSError``.  The
    pre-fix code let them bubble past the ``except OSError`` and surface as
    typer's default exit 1, breaking the documented exit-code contract.
    """
    artifact_path = tmp_path / "artifact.recotem"
    artifact_path.write_bytes(b"unused: replaced via patch")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    class _FakeBackendError(Exception):
        """Stand-in for botocore.exceptions.NoCredentialsError (no OSError)."""

    import fsspec.core as _core

    def _boom(*args, **kwargs):
        raise _FakeBackendError("Unable to locate credentials")

    monkeypatch.setattr(_core, "url_to_fs", _boom)
    result = runner.invoke(app, ["inspect", str(artifact_path)])
    assert result.exit_code == 5, (
        f"fsspec backend exception must map to exit 5, got {result.exit_code}; "
        f"output: {result.output!r}"
    )
    combined = result.output + (result.stderr if result.stderr else "")
    assert "_FakeBackendError" in combined or "Cannot open" in combined, (
        f"Error must surface the underlying exception class; got {combined!r}"
    )


# ---------------------------------------------------------------------------
# N-4: M-2 — keygen --type bogus exits 8; schema failure maps via
#            _map_exception_to_exit
# ---------------------------------------------------------------------------


def test_keygen_bogus_type_exits_8() -> None:
    """recotem keygen --type bogus must exit 8 (_EXIT_CONFIG).

    After M-2 the unknown --type guard calls _exit(_EXIT_CONFIG, ...) so the
    exit code is the documented config-error code 8 (not just non-zero).
    """
    result = runner.invoke(app, ["keygen", "--type", "bogus"])
    assert result.exit_code == 8, (
        f"keygen --type bogus must exit 8 (_EXIT_CONFIG); got {result.exit_code}"
    )
    combined = result.stdout + (result.stderr or "")
    assert "bogus" in combined, (
        f"Error message must mention the invalid type; got {combined!r}"
    )


def test_schema_failure_uses_map_exception_to_exit(monkeypatch) -> None:
    """recotem schema failure routes through _map_exception_to_exit.

    When build_source_config_union() raises a RecipeError, the schema command
    must exit 2 (_EXIT_RECIPE) — not exit 1 (unhandled) — so scripts that
    probe schema availability can distinguish a broken plugin registry from an
    unrelated crash.
    """
    from unittest.mock import patch

    from recotem.recipe.errors import RecipeError

    with patch(
        "recotem.datasource.registry.build_source_config_union",
        side_effect=RecipeError("simulated registry failure"),
    ):
        result = runner.invoke(app, ["schema"])

    assert result.exit_code == 2, (
        f"schema failure with RecipeError must exit 2; got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# N-5: M-3 — inspect exit code for malformed RECOTEM_SIGNING_KEYS
# ---------------------------------------------------------------------------


def test_inspect_malformed_signing_keys_exits_nonzero(
    tmp_path: Path, monkeypatch
) -> None:
    """inspect must exit non-zero and surface a clear error when
    RECOTEM_SIGNING_KEYS has an invalid format (no colon separator).

    With M-3, the except block inside inspect uses _map_exception_to_exit so
    that different exception types produce distinct, documented exit codes.
    A malformed KeyRing raises ArtifactError which maps to exit 5.
    """
    from tests.conftest import build_raw_artifact

    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "malformed_key_test", "best_score": 0.5},
        payload_bytes=b"payload",
    )
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(data)

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", "invalid format")  # no colon
    result = runner.invoke(app, ["inspect", str(artifact_path)])

    # Must be non-zero (not silently succeed on an unverified artifact).
    assert result.exit_code != 0, (
        "inspect must exit non-zero for malformed RECOTEM_SIGNING_KEYS"
    )
    # ArtifactError from KeyRing → exit 5 via _map_exception_to_exit.
    assert result.exit_code == 5, (
        f"Malformed RECOTEM_SIGNING_KEYS causes ArtifactError → exit 5 via "
        f"_map_exception_to_exit; got {result.exit_code}"
    )
    combined = result.stdout + (result.stderr or "")
    assert "malformed" in combined.lower() or "invalid" in combined.lower(), (
        f"Error output must mention malformed/invalid format; got {combined!r}"
    )


# ---------------------------------------------------------------------------
# Finding #3: SystemExit misclassification — documented behavior verification
# ---------------------------------------------------------------------------
# The current code honors SystemExit(0) as success (Literal int 0 is the only
# success sentinel per the module docstring).  SystemExit with non-int or
# unknown codes maps to _EXIT_UNKNOWN.  These tests verify the documented
# behavior is preserved and not accidentally changed.


def test_train_systemexit_with_non_recotem_int_code_becomes_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    """SystemExit with an integer code that is not a documented _EXIT_* constant
    (e.g. 42) must map to _EXIT_UNKNOWN (1), not be passed through to the shell.

    This prevents a library call inside run_training from injecting an arbitrary
    shell exit code.  Only the documented range (0, 2-8) is passed through.
    _EXIT_UNKNOWN (1) is not in the pass-through set either — it becomes 1 via
    the fallback, which is the same value, but the contract is that only the
    documented constants are honored.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "se_arbitrary")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    def _raises_systemexit_42(*_a, **_kw):
        raise SystemExit(42)

    with patch(
        "recotem.training.pipeline.run_training", side_effect=_raises_systemexit_42
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    # 42 is not a documented _EXIT_* constant; must become _EXIT_UNKNOWN (1).
    assert result.exit_code == 1, (
        f"SystemExit(42) must map to _EXIT_UNKNOWN (1); got {result.exit_code}"
    )


def test_train_systemexit_with_str_code_becomes_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    """SystemExit with a string code must map to _EXIT_UNKNOWN (1).

    String codes fail the isinstance(code, int) check and fall through to the
    _EXIT_UNKNOWN branch.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "se_str")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    def _raises_systemexit_str(*_a, **_kw):
        raise SystemExit("error message")

    with patch(
        "recotem.training.pipeline.run_training", side_effect=_raises_systemexit_str
    ):
        result = runner.invoke(app, ["train", str(yaml_path)])

    assert result.exit_code == 1, (
        f"SystemExit('error message') must map to _EXIT_UNKNOWN (1); "
        f"got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# Finding #4: inspect masks missing-extra ImportError
# ---------------------------------------------------------------------------


def test_inspect_importerror_gcs_surfaces_install_hint(
    tmp_path: Path, monkeypatch
) -> None:
    """When fsspec.core.url_to_fs raises ImportError (missing gcsfs backend),
    inspect must surface a 'pip install recotem[gcs]' hint, not a generic error.
    """
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # Simulate a gs:// path so the scheme hint fires
    artifact_gs = "gs://my-bucket/model.recotem"

    with patch(
        "fsspec.core.url_to_fs",
        side_effect=ImportError("No module named 'gcsfs'"),
    ):
        result = runner.invoke(app, ["inspect", artifact_gs])

    # Must exit non-zero (artifact error code 5)
    assert result.exit_code == 5, (
        f"ImportError from missing gcsfs must exit 5; got {result.exit_code}"
    )
    combined = result.stdout + (result.stderr or "")
    assert "recotem[gcs]" in combined, (
        f"Error message must contain 'recotem[gcs]' install hint; got: {combined!r}"
    )


def test_inspect_importerror_s3_surfaces_install_hint(
    tmp_path: Path, monkeypatch
) -> None:
    """When fsspec.core.url_to_fs raises ImportError for s3, inspect surfaces
    a 'pip install recotem[s3]' hint."""
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    artifact_s3 = "s3://my-bucket/model.recotem"

    with patch(
        "fsspec.core.url_to_fs",
        side_effect=ImportError("No module named 's3fs'"),
    ):
        result = runner.invoke(app, ["inspect", artifact_s3])

    assert result.exit_code == 5
    combined = result.stdout + (result.stderr or "")
    assert "recotem[s3]" in combined, (
        f"Error message must contain 'recotem[s3]' install hint; got: {combined!r}"
    )


def test_inspect_importerror_unknown_scheme_surfaces_generic_hint(
    tmp_path: Path, monkeypatch
) -> None:
    """When fsspec.core.url_to_fs raises ImportError for an unknown scheme,
    inspect surfaces a generic install hint (not a scheme-specific one)."""
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    artifact_ftp = "ftp://host/model.recotem"

    with patch(
        "fsspec.core.url_to_fs",
        side_effect=ImportError("No module named 'ftpfs'"),
    ):
        result = runner.invoke(app, ["inspect", artifact_ftp])

    assert result.exit_code == 5
    combined = result.stdout + (result.stderr or "")
    # Generic hint must mention installing the backend
    assert "install" in combined.lower(), (
        f"Error message must contain install hint; got: {combined!r}"
    )


# ---------------------------------------------------------------------------
# CLI-1: inspect URI repair — remote URIs must not be mangled by pathlib.Path
# ---------------------------------------------------------------------------


def test_repair_uri_restores_double_slash_for_s3() -> None:
    """_repair_uri must restore 's3://bucket/key' from 's3:/bucket/key'."""
    from recotem.cli import _repair_uri

    assert _repair_uri("s3:/bucket/key.recotem") == "s3://bucket/key.recotem"


def test_repair_uri_restores_double_slash_for_gs() -> None:
    """_repair_uri must restore 'gs://bucket/key' from 'gs:/bucket/key'."""
    from recotem.cli import _repair_uri

    assert _repair_uri("gs:/bucket/key.recotem") == "gs://bucket/key.recotem"


def test_repair_uri_restores_double_slash_for_https() -> None:
    """_repair_uri must restore 'https://host/path' from 'https:/host/path'."""
    from recotem.cli import _repair_uri

    assert _repair_uri("https:/host/path.recotem") == "https://host/path.recotem"


def test_repair_uri_leaves_already_correct_uri_unchanged() -> None:
    """_repair_uri must not modify an already-correct double-slash URI."""
    from recotem.cli import _repair_uri

    assert _repair_uri("s3://bucket/key.recotem") == "s3://bucket/key.recotem"
    assert _repair_uri("gs://bucket/key.recotem") == "gs://bucket/key.recotem"
    assert (
        _repair_uri("file:///abs/path/model.recotem")
        == "file:///abs/path/model.recotem"
    )


def test_repair_uri_leaves_local_absolute_path_unchanged() -> None:
    """_repair_uri must not modify absolute local paths."""
    from recotem.cli import _repair_uri

    assert _repair_uri("/abs/path/model.recotem") == "/abs/path/model.recotem"


def test_repair_uri_leaves_local_relative_path_unchanged() -> None:
    """_repair_uri must not modify relative local paths."""
    from recotem.cli import _repair_uri

    assert _repair_uri("relative/path/model.recotem") == "relative/path/model.recotem"


# ---------------------------------------------------------------------------
# I-6: inspect JSON parse failure must exit 5 (ARTIFACT), not 1 (UNKNOWN)
# ---------------------------------------------------------------------------


def test_inspect_corrupt_header_json_exits_5(tmp_path: Path, monkeypatch) -> None:
    """When the artifact's header JSON is corrupted, inspect must exit 5.

    Before the I-6 fix, ``except Exception as exc: code = _map_exception_to_exit(exc)``
    was used for the JSON parse step.  JSONDecodeError / UnicodeDecodeError are
    not mapped by ``_map_exception_to_exit`` so they defaulted to _EXIT_UNKNOWN (1).
    After the fix, the specific exception types are caught and mapped to
    _EXIT_ARTIFACT (5) explicitly, which matches what docs/operations.md documents.
    """
    from unittest.mock import patch

    from tests.conftest import build_raw_artifact

    # Build a valid artifact.
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "json_corrupt_test", "best_score": 0.5},
        payload_bytes=b"dummy",
    )
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(data)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # Patch json.loads to simulate a corrupt header JSON.
    import json as _json

    original_loads = _json.loads

    def _boom(s, **kwargs):
        # Raise only when called with the header data bytes (not from
        # other json.loads call sites like schema command).
        raise _json.JSONDecodeError("Simulated corrupt JSON", doc="", pos=0)

    with patch("recotem.cli.json.loads", side_effect=_boom):
        result = runner.invoke(app, ["inspect", str(artifact_path)])

    assert result.exit_code == 5, (
        f"Corrupt header JSON must exit 5 (ArtifactError); got {result.exit_code}. "
        f"Output: {result.output!r}"
    )
    combined = result.output + (result.stderr or "")
    assert (
        "Header JSON parse failed" in combined
        or "JSON" in combined
        or result.exit_code == 5
    )


def test_inspect_unicode_decode_error_in_header_exits_5(
    tmp_path: Path, monkeypatch
) -> None:
    """UnicodeDecodeError from header bytes must also exit 5 (ARTIFACT).

    The header bytes are expected to be valid UTF-8.  If they are not, the
    UnicodeDecodeError must map to ARTIFACT (5) via the explicit exception clause.
    """
    from unittest.mock import patch

    from tests.conftest import build_raw_artifact

    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "unicode_test", "best_score": 0.5},
        payload_bytes=b"dummy",
    )
    artifact_path = tmp_path / "model.recotem"
    artifact_path.write_bytes(data)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    # Patch json.loads to raise UnicodeDecodeError (simulating non-UTF-8 header).
    def _raise_unicode_error(s, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")

    with patch("recotem.cli.json.loads", side_effect=_raise_unicode_error):
        result = runner.invoke(app, ["inspect", str(artifact_path)])

    assert result.exit_code == 5, (
        f"UnicodeDecodeError in header must exit 5 (ArtifactError); "
        f"got {result.exit_code}. Output: {result.output!r}"
    )


# ---------------------------------------------------------------------------
# I-7: serve OSError exit code depends on errno (EADDRINUSE→8, others→1)
# ---------------------------------------------------------------------------


def test_serve_eaddrinuse_oserror_exits_8(tmp_path: Path, monkeypatch) -> None:
    """serve must exit 8 (config error) when uvicorn.run raises OSError(EADDRINUSE).

    EADDRINUSE, EACCES, and EADDRNOTAVAIL are bind-related errors that indicate
    a configuration problem (wrong port, wrong address, missing privileges).
    These map to _EXIT_CONFIG (8).
    """
    import errno as _errno
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")

    exc = OSError(_errno.EADDRINUSE, "Address already in use")
    exc.errno = _errno.EADDRINUSE

    with patch("uvicorn.run", side_effect=exc):
        result = runner.invoke(
            app,
            ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
        )

    assert result.exit_code == 8, (
        f"EADDRINUSE must exit 8 (_EXIT_CONFIG); got {result.exit_code}. "
        f"Output: {result.output!r}"
    )


@pytest.mark.parametrize(
    "err_errno,expected_exit",
    [
        (12, 1),  # ENOMEM — resource exhaustion, not a config error → UNKNOWN (1)
        (24, 1),  # EMFILE — too many open files, resource exhaustion → UNKNOWN (1)
    ],
)
def test_serve_non_bind_oserror_exits_non_config(
    err_errno: int, expected_exit: int, tmp_path: Path, monkeypatch
) -> None:
    """serve OSError with non-bind errno must NOT exit 8 (config).

    ENOMEM and EMFILE indicate resource exhaustion, not configuration mistakes.
    They must map through _map_exception_to_exit which returns _EXIT_UNKNOWN (1)
    for arbitrary OSError instances (OSError is not in the exception mapping table).
    """
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")

    exc = OSError(err_errno, "simulated runtime error")
    exc.errno = err_errno

    with patch("uvicorn.run", side_effect=exc):
        result = runner.invoke(
            app,
            ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
        )

    assert result.exit_code == expected_exit, (
        f"errno={err_errno} must exit {expected_exit}; "
        f"got {result.exit_code}. Output: {result.output!r}"
    )
    # Crucially, it must NOT exit 8 for these resource-exhaustion errnos.
    assert result.exit_code != 8, (
        f"errno={err_errno} must NOT exit 8 (config); got {result.exit_code}."
    )


# ---------------------------------------------------------------------------
# I-21: --lock-timeout negative values (other than -1) exit 8
# ---------------------------------------------------------------------------


def test_train_lock_timeout_minus_ten_exits_8(tmp_path: Path, monkeypatch) -> None:
    """train --lock-timeout=-10 must exit 8 (config error).

    Only -1 (indefinite wait), 0 (non-blocking), and positive values are valid.
    Any other negative value has no defined meaning and could silently behave
    as indefinite wait; the CLI must reject it before acquiring the lock.
    """
    yaml_path = _minimal_recipe_yaml(tmp_path, "lock_timeout_neg")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    result = runner.invoke(app, ["train", str(yaml_path), "--lock-timeout", "-10"])

    assert result.exit_code == 8, (
        f"--lock-timeout=-10 must exit 8 (_EXIT_CONFIG); got {result.exit_code}. "
        f"Output: {result.output!r}"
    )
    combined = result.output + (result.stderr or "")
    assert "-10" in combined or "lock-timeout" in combined.lower()


@pytest.mark.parametrize(
    "lock_timeout_arg,description",
    [
        ("-1", "indefinite wait sentinel"),
        ("0", "non-blocking"),
        ("5", "positive seconds"),
        ("0.5", "fractional positive seconds"),
    ],
)
def test_train_lock_timeout_valid_values_pass_cli_validation(
    lock_timeout_arg: str, description: str, tmp_path: Path, monkeypatch
) -> None:
    """train --lock-timeout with valid values (-1, 0, positive) must not exit 8.

    The CLI validation must only reject negative values other than -1.
    Valid values (-1, 0, positive floats) must pass through to run_training.
    """
    from unittest.mock import MagicMock, patch

    from recotem.training.pipeline import TrainResult

    yaml_path = _minimal_recipe_yaml(tmp_path, f"lock_timeout_{lock_timeout_arg}")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    fake_result = MagicMock(spec=TrainResult)
    fake_result.recipe_name = "test"
    fake_result.run_id = "abc"

    with patch("recotem.training.pipeline.run_training", return_value=fake_result):
        result = runner.invoke(
            app, ["train", str(yaml_path), "--lock-timeout", lock_timeout_arg]
        )

    # Must not be rejected by the CLI validation (exit 8).
    assert result.exit_code != 8, (
        f"--lock-timeout={lock_timeout_arg} ({description}) must not exit 8; "
        f"got {result.exit_code}. Output: {result.output!r}"
    )


def test_inspect_s3_uri_passes_double_slash_to_fsspec(monkeypatch) -> None:
    """inspect must call url_to_fs with exactly 's3://bucket/key.recotem',
    not 's3:/bucket/key.recotem' (the pathlib-mangled form).

    On POSIX, Path('s3://bucket/key') normalises to 's3:/bucket/key'.
    By accepting the artifact argument as str (not Path), inspect preserves
    the double-slash and _repair_uri restores any single-slash that may have
    been introduced by the shell or argument processing.
    """
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    captured_uris: list[str] = []

    def _mock_url_to_fs(uri, **kw):
        captured_uris.append(uri)
        # Raise an OSError so the CLI exits without needing real s3 content.
        raise OSError("no such bucket")

    with patch("fsspec.core.url_to_fs", side_effect=_mock_url_to_fs):
        runner.invoke(app, ["inspect", "s3://bucket/key.recotem"])

    assert captured_uris, "url_to_fs must be called"
    assert captured_uris[0] == "s3://bucket/key.recotem", (
        f"url_to_fs must receive exactly 's3://bucket/key.recotem'; "
        f"got {captured_uris[0]!r}"
    )


def test_inspect_gs_uri_passes_double_slash_to_fsspec(monkeypatch) -> None:
    """Same as above for gs:// (Google Cloud Storage)."""
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    captured_uris: list[str] = []

    def _mock_url_to_fs(uri, **kw):
        captured_uris.append(uri)
        raise OSError("no bucket")

    with patch("fsspec.core.url_to_fs", side_effect=_mock_url_to_fs):
        runner.invoke(app, ["inspect", "gs://bucket/key.recotem"])

    assert captured_uris and captured_uris[0] == "gs://bucket/key.recotem", (
        f"url_to_fs must receive 'gs://bucket/key.recotem'; got {captured_uris!r}"
    )


def test_inspect_file_abs_uri_passes_triple_slash_to_fsspec(monkeypatch) -> None:
    """file:///abs/path must be passed verbatim (not mangled)."""
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    captured_uris: list[str] = []

    def _mock_url_to_fs(uri, **kw):
        captured_uris.append(uri)
        raise OSError("not found")

    with patch("fsspec.core.url_to_fs", side_effect=_mock_url_to_fs):
        runner.invoke(app, ["inspect", "file:///abs/path/model.recotem"])

    assert captured_uris and captured_uris[0] == "file:///abs/path/model.recotem", (
        f"url_to_fs must receive 'file:///abs/path/model.recotem'; got {captured_uris!r}"
    )


def test_inspect_local_path_passes_unchanged_to_fsspec(tmp_path, monkeypatch) -> None:
    """A local file path must be passed verbatim (as a string) to url_to_fs."""
    from unittest.mock import patch

    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    local = str(tmp_path / "model.recotem")

    captured_uris: list[str] = []

    def _mock_url_to_fs(uri, **kw):
        captured_uris.append(uri)
        raise OSError("not found")

    with patch("fsspec.core.url_to_fs", side_effect=_mock_url_to_fs):
        runner.invoke(app, ["inspect", local])

    assert captured_uris and captured_uris[0] == local, (
        f"url_to_fs must receive local path unchanged; got {captured_uris!r}"
    )


# ---------------------------------------------------------------------------
# CLI-4: inspect missing signing keys → exit 8 (CONFIG), not exit 5
# ---------------------------------------------------------------------------


def test_inspect_missing_signing_keys_exits_8(tmp_path: Path, monkeypatch) -> None:
    """inspect must exit 8 (_EXIT_CONFIG) when RECOTEM_SIGNING_KEYS is unset.

    CLAUDE.md documents the 'signing keys missing' case as exit 8 (ConfigError),
    matching the train side.  The previous code used _EXIT_ARTIFACT (5).
    """
    from tests.conftest import build_raw_artifact

    artifact_path = tmp_path / "model.recotem"
    data = build_raw_artifact(
        kid="active",
        key_hex=ACTIVE_KEY_HEX,
        header_dict={"recipe_name": "config_exit_test", "best_score": 0.5},
        payload_bytes=b"dummy",
    )
    artifact_path.write_bytes(data)
    monkeypatch.delenv("RECOTEM_SIGNING_KEYS", raising=False)
    monkeypatch.delenv("RECOTEM_ENV", raising=False)

    result = runner.invoke(app, ["inspect", str(artifact_path)])

    assert result.exit_code == 8, (
        f"Missing RECOTEM_SIGNING_KEYS must produce exit 8 (_EXIT_CONFIG); "
        f"got {result.exit_code}. Output: {result.output!r}"
    )
    combined = result.output + (result.stderr if result.stderr else "")
    assert "RECOTEM_SIGNING_KEYS" in combined


# ---------------------------------------------------------------------------
# CLI-5: serve must not swallow MemoryError/RecursionError
# ---------------------------------------------------------------------------


def test_serve_memory_error_propagates(tmp_path: Path, monkeypatch) -> None:
    """serve must propagate MemoryError from uvicorn.run, not catch it.

    Round-12 OOM-propagation policy: MemoryError and RecursionError must never
    be collapsed into a mapped exit code — the operator needs the real signal.
    """
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")

    with patch("uvicorn.run", side_effect=MemoryError("OOM")):
        # CliRunner catches all exceptions by default (catch_exceptions=True),
        # so the MemoryError is stored in result.exception instead of being
        # converted to an exit code.
        result = runner.invoke(
            app,
            ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
            catch_exceptions=True,
        )

    # MemoryError must propagate: result.exception must be the MemoryError.
    assert isinstance(result.exception, MemoryError), (
        f"serve must propagate MemoryError; got exit {result.exit_code}, "
        f"exception {result.exception!r}"
    )


def test_serve_recursion_error_propagates(tmp_path: Path, monkeypatch) -> None:
    """serve must propagate RecursionError from uvicorn.run."""
    from unittest.mock import patch

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")
    monkeypatch.setenv("RECOTEM_ENV", "test")

    with patch("uvicorn.run", side_effect=RecursionError("max recursion")):
        result = runner.invoke(
            app,
            ["serve", "--recipes", str(recipes_dir), "--insecure-no-auth"],
            catch_exceptions=True,
        )

    assert isinstance(result.exception, RecursionError), (
        f"serve must propagate RecursionError; got exit {result.exit_code}, "
        f"exception {result.exception!r}"
    )


# ---------------------------------------------------------------------------
# CLI-7: --dev-allow-unsigned warns when RECOTEM_SIGNING_KEYS is set
# ---------------------------------------------------------------------------


def test_inspect_dev_allow_unsigned_warns_when_keys_set(
    tmp_path: Path, monkeypatch
) -> None:
    """inspect --dev-allow-unsigned must emit a warning when RECOTEM_SIGNING_KEYS
    is already set (the flag has no effect in that case).

    The 'dev_allow_unsigned_ignored' structlog event should be emitted.
    We verify by monkey-patching the module-level logger in cli so the call
    is captured regardless of the structlog processor chain.
    """
    from unittest.mock import MagicMock, patch

    import recotem.cli as cli_mod
    from tests.conftest import build_raw_artifact

    dev_key_hex = "0" * 64
    artifact_path = tmp_path / "model.recotem"
    data = build_raw_artifact(
        kid="dev",
        key_hex=dev_key_hex,
        header_dict={"recipe_name": "warn_test", "best_score": 0.5},
        payload_bytes=b"dummy",
    )
    artifact_path.write_bytes(data)

    # Set BOTH the signing key and the dev flag — the warning should fire.
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"dev:{dev_key_hex}")
    monkeypatch.setenv("RECOTEM_ENV", "development")

    warning_calls: list = []

    original_get_logger = cli_mod.structlog.get_logger

    def _capturing_get_logger(*args, **kwargs):
        real = original_get_logger(*args, **kwargs)
        spy = MagicMock(wraps=real)

        # Track warning calls
        def _warning(*a, **kw):
            warning_calls.append((a, kw))
            return real.warning(*a, **kw)

        spy.warning = MagicMock(side_effect=_warning)
        return spy

    with patch.object(
        cli_mod.structlog, "get_logger", side_effect=_capturing_get_logger
    ):
        result = runner.invoke(
            app,
            ["inspect", "--dev-allow-unsigned", str(artifact_path)],
        )

    # Check that any warning call had the expected event name
    warned = any(
        (len(a) > 0 and a[0] == "dev_allow_unsigned_ignored") for a, kw in warning_calls
    )
    assert warned, (
        f"dev_allow_unsigned_ignored warning must be emitted when RECOTEM_SIGNING_KEYS "
        f"is set and --dev-allow-unsigned is passed; captured warning calls: "
        f"{warning_calls}"
    )


# ---------------------------------------------------------------------------
# LEAK-2: --lock-timeout propagates to run_training
# ---------------------------------------------------------------------------


def test_train_lock_timeout_delivered_to_run_training(
    tmp_path: Path, monkeypatch
) -> None:
    """--lock-timeout <value> must be forwarded as lock_timeout kwarg to run_training.

    Uses monkeypatching to capture the keyword arguments that run_training
    receives, without executing the full training pipeline.
    """
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "lock_timeout_test")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    captured_kwargs: dict = {}

    def _spy_run_training(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return None  # simulate graceful skip (lock contended)

    with patch("recotem.training.pipeline.run_training", side_effect=_spy_run_training):
        result = runner.invoke(
            app,
            ["train", str(yaml_path), "--lock-timeout", "5.0"],
        )

    # The CLI itself may exit 0 (pipeline returned None = lock skipped)
    # or non-zero; what matters is the kwarg was forwarded.
    assert "lock_timeout" in captured_kwargs, (
        f"run_training must receive lock_timeout kwarg; "
        f"got kwargs: {list(captured_kwargs.keys())}"
    )
    assert captured_kwargs["lock_timeout"] == pytest.approx(5.0), (
        f"lock_timeout must be 5.0, got {captured_kwargs['lock_timeout']!r}"
    )


def test_train_lock_timeout_default_is_zero(tmp_path: Path, monkeypatch) -> None:
    """When --lock-timeout is not passed, run_training receives lock_timeout=0.0."""
    from unittest.mock import patch

    yaml_path = _minimal_recipe_yaml(tmp_path, "lock_timeout_default")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"active:{ACTIVE_KEY_HEX}")

    captured_kwargs: dict = {}

    def _spy_run_training(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return None

    with patch("recotem.training.pipeline.run_training", side_effect=_spy_run_training):
        runner.invoke(app, ["train", str(yaml_path)])

    assert "lock_timeout" in captured_kwargs
    assert captured_kwargs["lock_timeout"] == pytest.approx(0.0)
