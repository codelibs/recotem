"""``validate`` and ``train`` must report the same exit code for the same failure.

Measured on `main`: a plugin whose ``__init__`` raises ``ImportError`` — the
exact case ``docs/plugin-authoring.md`` tells authors to guard against — gave

    recotem train     -> exit 3   "Data fetch failed: No module named 'x'"
    recotem validate  -> exit 1   "DataSource probe failed [source]: No module named 'x'"

``train``'s pipeline wraps anything unrecognised from the datasource path as
``DataSourceError``; ``validate``'s ``_probe_source`` mapped the raw exception,
and a raw exception usually has no mapping, so it landed on ``_EXIT_UNKNOWN``.

Exit 1 is documented as "unhandled / unmapped exception", so a pre-flight
failure that ``train`` classifies correctly reached supervisors and CI as an
internal error of recotem itself. It is also precisely the drift ``cli.py``'s own
``_check_algorithms`` docstring warns against: validate exists to ask the same
question as train, and an answer that differs in exit code is not the same
answer.

The tests drive the real CLI rather than calling helpers, because the exit code
is a property of the process, and they assert **parity** (the two codes are
equal) as well as the value, so the pair cannot drift apart again in either
direction.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from recotem._exit_codes import (
    _EXIT_DATASOURCE,
    _EXIT_HTTP_FETCH,
    _EXIT_UNKNOWN,
)
from recotem.cli import app

runner = CliRunner()

_SIGNING = "dev:" + "ab" * 32


def _recipe(tmp_path: Path, source_block: str, name: str = "parity") -> Path:
    yaml_path = tmp_path / f"{name}.yaml"
    yaml_path.write_text(
        f"name: {name}\n"
        "source:\n"
        f"{source_block}"
        "schema:\n"
        "  user_column: user_id\n"
        "  item_column: item_id\n"
        "training:\n"
        "  algorithms: [TopPop]\n"
        "  cutoff: 3\n"
        "  n_trials: 1\n"
        "output:\n"
        f"  path: {tmp_path / (name + '.recotem')}\n"
    )
    return yaml_path


def _codes(yaml_path: Path, monkeypatch) -> tuple[int, int]:
    """Return ``(train_exit, validate_exit)`` for the same recipe."""
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    train_result = runner.invoke(app, ["train", str(yaml_path)])
    validate_result = runner.invoke(app, ["validate", str(yaml_path)])
    return train_result.exit_code, validate_result.exit_code


# ---------------------------------------------------------------------------
# The measured divergence.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "label"),
    [
        (ImportError("No module named 'my_optional_dep'"), "ImportError"),
        (RuntimeError("ctor blew up"), "RuntimeError"),
        (ValueError("bad config value"), "ValueError"),
        (KeyError("missing_key"), "KeyError"),
        (AttributeError("no attribute 'foo'"), "AttributeError"),
    ],
)
def test_init_failure_reports_the_same_code_from_both_commands(
    tmp_path: Path, monkeypatch, exc: Exception, label: str
) -> None:
    """An exception escaping a source's ``__init__``.

    This is the case ``plugin-authoring.md`` documents: a source that defers an
    optional-dependency import to ``__init__`` and forgets to wrap the
    ``ImportError``.  Exercised by making a *real, registered* source's
    ``__init__`` raise, so the recipe loads normally and both commands reach
    construction — which is the code path under test.  Registering a synthetic
    plugin instead would fail at recipe load with exit 2 from both commands and
    prove nothing.
    """
    from recotem.datasource.csv import CSVSource

    def boom(self, config):  # noqa: ANN001, ANN202, ARG001
        raise exc

    monkeypatch.setattr(CSVSource, "__init__", boom)

    csv = tmp_path / "data.csv"
    csv.write_text("user_id,item_id\nu1,i1\n")
    yaml_path = _recipe(tmp_path, f"  type: csv\n  path: {csv}\n", f"boom_{label}")
    train_code, validate_code = _codes(yaml_path, monkeypatch)

    assert train_code == validate_code, (
        f"{label} escaping __init__: train reported {train_code}, validate "
        f"reported {validate_code} — the two commands must agree"
    )
    assert validate_code == _EXIT_DATASOURCE, (
        f"{label} escaping __init__ must be exit 3, got {validate_code}"
    )
    assert validate_code != _EXIT_UNKNOWN


def test_probe_failure_reports_the_same_code_from_both_commands(
    tmp_path: Path, monkeypatch
) -> None:
    """The optional ``probe()`` hook, which only ``validate`` calls.

    ``train`` never calls ``probe()``, so parity here means: the failure
    ``validate`` surfaces from ``probe()`` must carry the same code ``train``
    would give for the equivalent failure during fetch — exit 3, not 1.
    """
    from recotem.datasource.csv import CSVSource

    def boom(self):  # noqa: ANN001, ANN202
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(CSVSource, "probe", boom)

    csv = tmp_path / "data.csv"
    csv.write_text("user_id,item_id\nu1,i1\n")
    yaml_path = _recipe(tmp_path, f"  type: csv\n  path: {csv}\n", "probe_boom")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == _EXIT_DATASOURCE, (
        f"a probe() failure must be exit 3, got {result.exit_code}:\n{result.output}"
    )


def test_probe_columns_failure_is_also_exit_3(tmp_path: Path, monkeypatch) -> None:
    """The second hook on the same path, fixed for the same reason."""
    from recotem.datasource.csv import CSVSource

    def boom(self, ctx):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError("probe_columns blew up")

    monkeypatch.setattr(CSVSource, "probe_columns", boom)

    csv = tmp_path / "data.csv"
    csv.write_text("user_id,item_id\nu1,i1\n")
    yaml_path = _recipe(tmp_path, f"  type: csv\n  path: {csv}\n", "cols_boom")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == _EXIT_DATASOURCE, (
        f"a probe_columns() failure must be exit 3, got {result.exit_code}:"
        f"\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Parity must not be bought by flattening codes that were already right.
# ---------------------------------------------------------------------------


def test_missing_csv_still_reports_3_from_both(tmp_path: Path, monkeypatch) -> None:
    """A built-in source failing normally: unchanged, and still in agreement."""
    yaml_path = _recipe(
        tmp_path, f"  type: csv\n  path: {tmp_path / 'absent.csv'}\n", "missing_csv"
    )
    train_code, validate_code = _codes(yaml_path, monkeypatch)
    assert (train_code, validate_code) == (_EXIT_DATASOURCE, _EXIT_DATASOURCE)


def test_http_fetch_failure_still_reports_7_not_3(tmp_path: Path, monkeypatch) -> None:
    """The wrap must not flatten exit 7 into exit 3.

    ``_map_exception_to_exit`` walks ``__cause__`` for ``HttpFetchError``, so
    wrapping with ``from exc`` preserves it.  Dropping the ``from exc`` — or
    catching ``HttpFetchError`` and re-raising it bare — would silently
    reclassify every SSRF / byte-cap / redirect refusal as a structural
    datasource error, which retry logic treats differently.
    """
    monkeypatch.delenv("RECOTEM_HTTP_ALLOW_PRIVATE", raising=False)
    sha = "0" * 64
    yaml_path = _recipe(
        tmp_path,
        f'  type: csv\n  path: "http://127.0.0.1:9/x.csv"\n  sha256: "{sha}"\n',
        "ssrf",
    )
    train_code, validate_code = _codes(yaml_path, monkeypatch)
    assert train_code == validate_code, (
        f"train reported {train_code}, validate {validate_code}"
    )
    assert validate_code == _EXIT_HTTP_FETCH, (
        f"an SSRF refusal must stay exit 7, got {validate_code} — the "
        "__cause__ chain has been broken"
    )


def test_valid_recipe_is_untouched(tmp_path: Path, monkeypatch) -> None:
    """Positive control: the wrap must not make a working recipe fail.

    Without this, a `_datasource_layer` that raised unconditionally would
    satisfy every assertion above.
    """
    csv = tmp_path / "ok.csv"
    rows = "\n".join(
        f"u{u},i{i}" for u in range(12) for i in range(6) if (u + i) % 2 == 0
    )
    csv.write_text("user_id,item_id\n" + rows + "\n")
    yaml_path = _recipe(tmp_path, f"  type: csv\n  path: {csv}\n", "good")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == 0, result.output
    assert "DataSource: probe OK (csv)" in result.output


def test_bare_http_fetch_error_from_a_source_still_reports_7(
    tmp_path: Path, monkeypatch
) -> None:
    """``from exc`` in the wrap is load-bearing, and this is what proves it.

    The built-in CSV source wraps ``HttpFetchError`` into ``DataSourceError``
    itself, so it reaches ``_datasource_layer`` already mapped and takes the
    passthrough branch — which means the SSRF test above passes with or without
    ``from exc`` and cannot guard it.  (It survived that mutation; this test is
    why the claim is now checked rather than asserted.)

    A third-party source under no such obligation can let ``HttpFetchError``
    escape raw.  Then the wrap is what decides the exit code:
    ``_map_exception_to_exit`` walks ``__cause__`` for ``HttpFetchError``, so
    ``from exc`` keeps it exit 7 while ``from None`` would silently reclassify
    it as exit 3 — turning a transient network refusal into a structural
    datasource error that retry logic treats differently.
    """
    from recotem._http_fetch import HttpFetchError
    from recotem.datasource.csv import CSVSource

    def boom(self):  # noqa: ANN001, ANN202
        raise HttpFetchError("Refusing fetch to private/internal address")

    monkeypatch.setattr(CSVSource, "probe", boom)

    csv = tmp_path / "data.csv"
    csv.write_text("user_id,item_id\nu1,i1\n")
    yaml_path = _recipe(tmp_path, f"  type: csv\n  path: {csv}\n", "bare_http")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code == _EXIT_HTTP_FETCH, (
        f"a bare HttpFetchError must stay exit 7, got {result.exit_code} — the "
        f"__cause__ chain has been broken:\n{result.output}"
    )


@pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
def test_oom_propagates_unwrapped(tmp_path: Path, monkeypatch, exc_cls) -> None:
    """``MemoryError`` / ``RecursionError`` must not be dressed as exit 3.

    The rest of the codebase carries the same ``except (MemoryError,
    RecursionError): raise`` clause (``pipeline.py``,
    ``datasource/bigquery.py``, ``_size_cap.py``) so a resource exhaustion is
    not reported as a domain error.

    What that buys *here* is narrower than "it propagates", and the narrower
    claim is the one asserted. ``validate``'s call site catches ``Exception``
    and maps it, so an OOM still ends as ``SystemExit`` — measured: exit 1,
    ``_EXIT_UNKNOWN``. The clause is what keeps it off **exit 3**: without it
    the wrap would relabel resource exhaustion as a data-source configuration
    problem and send the operator to their recipe instead of to the box.
    Exit 1 is the honest answer for an OOM; exit 3 is not.
    """
    from recotem.datasource.csv import CSVSource

    def boom(self):  # noqa: ANN001, ANN202
        raise exc_cls("resource exhausted")

    monkeypatch.setattr(CSVSource, "probe", boom)

    csv = tmp_path / "data.csv"
    csv.write_text("user_id,item_id\nu1,i1\n")
    yaml_path = _recipe(tmp_path, f"  type: csv\n  path: {csv}\n", "oom")
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", _SIGNING)
    result = runner.invoke(app, ["validate", str(yaml_path)])
    assert result.exit_code != _EXIT_DATASOURCE, (
        f"{exc_cls.__name__} was relabelled as a DataSourceError (exit 3); "
        "resource exhaustion is not a data-source configuration problem"
    )
    assert result.exit_code == _EXIT_UNKNOWN, (
        f"expected {exc_cls.__name__} to reach the unmapped-exception code, "
        f"got {result.exit_code}"
    )
