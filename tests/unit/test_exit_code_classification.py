"""Exit-code classification for source-path failures.

Two guards reuse :func:`recotem._http_fetch.assert_host_public` /
:func:`recotem._http_fetch.verify_sha256` on inputs that never travel over
HTTP.  Both signal with ``HttpFetchError``, and ``_map_exception_to_exit``
walks ``__cause__`` for that type, so chaining it silently reclassified the
failure as exit 7 (transient network) even though the raised exception is a
``DataSourceError`` whose own ``code`` attribute says ``datasource_error``.

Cron and CronJob retry logic branches on the exit code, so a permanent refusal
reported as a transient one is retried forever.  These tests pin the exit code
*and* the ``code`` attribute together, because their disagreement was half the
defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recotem._exit_codes import (
    _EXIT_DATASOURCE,
    _EXIT_HTTP_FETCH,
    _map_exception_to_exit,
)
from recotem.datasource.base import DataSourceError, FetchContext

# ---------------------------------------------------------------------------
# SQL DSN routing forms
# ---------------------------------------------------------------------------

# Every routing form the SSRF guard in ``recotem.datasource.sql`` refuses.
# The guard reaches one verdict — "this DSN would route somewhere we do not
# allow" — so every form must report the same exit code, whichever branch of
# the guard reached it.
_SQL_REFUSED_DSNS = [
    ("netloc-private-ipv4", "postgresql://u:p@10.0.0.5/db"),
    ("netloc-loopback-ipv4", "postgresql://u:p@127.0.0.1/db"),
    ("netloc-loopback-ipv6", "postgresql://u:p@[::1]/db"),
    ("netloc-link-local", "postgresql://u:p@169.254.169.254/db"),
    ("query-host-private", "postgresql:///db?host=10.0.0.5"),
    ("query-hostaddr-private", "postgresql:///db?hostaddr=127.0.0.1"),
    ("unresolvable-hostname", "postgresql://u:p@no-such-host.invalid/db"),
    ("libpq-service-file", "postgresql:///db?service=prod"),
    ("mysql-unix-socket", "mysql+pymysql:///db?unix_socket=/tmp/mysql.sock"),
    ("libpq-abs-path-host", "postgresql:///db?host=/var/run/postgresql"),
    ("network-dsn-no-host", "postgresql:///db"),
]


@pytest.mark.parametrize(
    "dsn",
    [dsn for _name, dsn in _SQL_REFUSED_DSNS],
    ids=[name for name, _dsn in _SQL_REFUSED_DSNS],
)
def test_sql_ssrf_refusal_exits_3_for_every_routing_form(
    monkeypatch: pytest.MonkeyPatch, dsn: str
) -> None:
    """All refused DSN routing forms report exit 3 and ``datasource_error``.

    Six of these forms used to report exit 7 because the guard chained the
    ``HttpFetchError`` raised by ``assert_host_public``, while the other five
    — the same guard, the same verdict, a different routing form — reported
    exit 3.  A SQL DSN is not an HTTP fetch: none of the ``RECOTEM_HTTP_*``
    settings apply to it and nothing here speaks HTTP.
    """
    from recotem.datasource.sql import SQLConfig, SQLSource

    monkeypatch.delenv("RECOTEM_SQL_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("RECOTEM_RECIPE_DB_DSN", dsn)

    cfg = SQLConfig(type="sql", dsn_env="RECOTEM_RECIPE_DB_DSN", query="SELECT 1")
    with pytest.raises(DataSourceError) as excinfo:
        SQLSource(cfg)

    exc = excinfo.value
    assert exc.code == "datasource_error", (
        f"{dsn!r} must report code='datasource_error'; got {exc.code!r}"
    )
    assert _map_exception_to_exit(exc) == _EXIT_DATASOURCE, (
        f"{dsn!r} must exit {_EXIT_DATASOURCE} like every other refused "
        f"routing form; got {_map_exception_to_exit(exc)}.  The exit code and "
        f"the code field must not disagree."
    )


# ---------------------------------------------------------------------------
# sha256 pin on a local vs. a network source path
# ---------------------------------------------------------------------------


def _write_csv(path: Path) -> None:
    path.write_text("user_id,item_id\nu1,i1\nu2,i2\n")


def test_local_path_sha256_mismatch_exits_3(tmp_path: Path) -> None:
    """A local file whose bytes do not match the pin is a data-source failure.

    Nothing was fetched over HTTP, so reporting exit 7 promised a transient
    network failure for a permanent content mismatch — and a cron treating 7
    as retryable would retry it forever.  Exit 3 matches the byte-cap and
    read-failure checks guarding the same local read.
    """
    from recotem.datasource.csv import CSVConfig, CSVSource

    data = tmp_path / "interactions.csv"
    _write_csv(data)

    cfg = CSVConfig(type="csv", path=str(data), sha256="00" * 32)
    ctx = FetchContext(recipe_name="local_sha", run_id="run-1")

    with pytest.raises(DataSourceError) as excinfo:
        CSVSource(cfg).fetch(ctx)

    exc = excinfo.value
    assert "sha256 mismatch" in str(exc)
    assert exc.code == "datasource_error"
    assert _map_exception_to_exit(exc) == _EXIT_DATASOURCE, (
        f"A local-path sha256 mismatch must exit {_EXIT_DATASOURCE}; "
        f"got {_map_exception_to_exit(exc)}"
    )


def test_network_path_sha256_mismatch_still_exits_7(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP source keeps exit 7 — the pin is the last step of that fetch.

    The counterpart to the test above: the two must stay coherent, and for a
    network fetch the mismatch belongs with the redirect, timeout and byte-cap
    failures of the same pipeline, all of which report exit 7.

    The ``code`` attribute stays ``datasource_error`` here.  That asymmetry is
    the documented behaviour of the HTTP path — ``docs/data-sources/csv.md``
    states that exit 7 takes precedence over ``DataSourceError`` in the
    exit-code chain — and is asserted so a change to it is deliberate.
    """
    from recotem.datasource import csv as csv_mod
    from recotem.datasource.csv import CSVConfig, CSVSource

    monkeypatch.setattr(
        csv_mod,
        "_fetch_http_bytes",
        lambda *a, **kw: b"user_id,item_id\nu1,i1\n",
    )

    cfg = CSVConfig(
        type="csv",
        path="https://example.invalid/interactions.csv",
        sha256="00" * 32,
    )
    ctx = FetchContext(recipe_name="network_sha", run_id="run-1")

    with pytest.raises(DataSourceError) as excinfo:
        CSVSource(cfg).fetch(ctx)

    exc = excinfo.value
    assert "sha256 mismatch" in str(exc)
    assert exc.code == "datasource_error"
    assert _map_exception_to_exit(exc) == _EXIT_HTTP_FETCH, (
        f"An http(s) sha256 mismatch must stay on exit {_EXIT_HTTP_FETCH} with "
        f"the rest of the fetch pipeline; got {_map_exception_to_exit(exc)}"
    )
