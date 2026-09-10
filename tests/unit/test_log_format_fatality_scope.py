"""A malformed ``RECOTEM_LOG_FORMAT`` stops some commands and not others.

``ServeConfig.from_env`` raises ``ConfigError`` on a value outside
auto/json/console, and ``tests/unit/test_config.py`` pins that.  What was not
pinned is *which commands reach it*: ``serve`` and ``inspect`` build a
``ServeConfig``, and ``train`` / ``validate`` / ``keygen`` / ``schema`` do not
-- they go through ``cli._configure_logging_from_env``, which treats anything
that is not json or console as ``auto`` and carries on.

The asymmetry is what an operator trips over, because both halves usually read
the same ConfigMap: one typo takes the serve Deployment down with exit 8 and
leaves the train CronJob running with a log format nobody chose.  Pin both
halves so the scope cannot drift, and so the sentence describing it in
``config.py`` stays checkable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from recotem._exit_codes import _EXIT_CONFIG, _EXIT_SUCCESS
from recotem.cli import app

runner = CliRunner()

_BAD = "jsonl"  # a plausible typo: not one of auto / json / console


def _recipe(tmp_path: Path) -> Path:
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")
    yaml_path = tmp_path / "r.yaml"
    yaml_path.write_text(
        "name: scope_probe\n"
        "source:\n"
        "  type: csv\n"
        f"  path: {csv_file}\n"
        "schema:\n"
        "  user_column: user_id\n"
        "  item_column: item_id\n"
        "training:\n"
        "  algorithms: [TopPop]\n"
        "output:\n"
        f"  path: {tmp_path / 'out.recotem'}\n"
    )
    return yaml_path


@pytest.mark.parametrize(
    "argv",
    [
        ["schema"],
        ["keygen", "--type", "api"],
        ["keygen", "--type", "signing"],
    ],
)
def test_commands_that_never_build_a_serveconfig_tolerate_a_bad_value(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", _BAD)
    result = runner.invoke(app, argv)
    assert result.exit_code == _EXIT_SUCCESS, (
        f"`recotem {' '.join(argv)}` exited {result.exit_code} on "
        f"RECOTEM_LOG_FORMAT={_BAD!r}. These commands configure logging "
        "best-effort and must not fail on it; if that changed deliberately, "
        "the scope paragraph in config.py has to change with it."
    )


def test_validate_tolerates_a_bad_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", _BAD)
    result = runner.invoke(app, ["validate", str(_recipe(tmp_path))])
    assert result.exit_code == _EXIT_SUCCESS, (
        f"`recotem validate` exited {result.exit_code} on "
        f"RECOTEM_LOG_FORMAT={_BAD!r}; it does not build a ServeConfig."
    )


def test_validate_is_not_simply_ignoring_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Positive control for the two tests above.

    They would pass just as well if ``validate`` ignored every environment
    variable, so show that it does read one that *is* fatal for it.
    """
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", "json")
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(tmp_path / "elsewhere"))
    result = runner.invoke(app, ["validate", str(_recipe(tmp_path))])
    assert result.exit_code != _EXIT_SUCCESS, (
        "validate accepted an output.path outside RECOTEM_ARTIFACT_ROOT, so "
        "the tolerance results above do not show that the log-format value "
        "was read and forgiven."
    )


def test_inspect_reaches_the_fatal_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``inspect`` builds a ServeConfig, so the same value is exit 8 there.

    The artifact does not have to exist: ``from_env`` runs before any I/O, and
    that ordering is the point -- the operator learns about the typo before
    the file is even opened.
    """
    monkeypatch.setenv("RECOTEM_LOG_FORMAT", _BAD)
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", "dev:" + "ab" * 32)
    result = runner.invoke(app, ["inspect", str(tmp_path / "missing.recotem")])
    assert result.exit_code == _EXIT_CONFIG, (
        f"`recotem inspect` exited {result.exit_code}, expected "
        f"{_EXIT_CONFIG} (configuration error) for "
        f"RECOTEM_LOG_FORMAT={_BAD!r}."
    )
