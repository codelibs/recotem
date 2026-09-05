"""``training.storage_path`` is pre-flighted, and its failure exits 8, not 1.

Two defects are guarded here, and they are independent:

1. **An unrecognised URL scheme was read as a filename.**  ``_make_storage``
   classified the value with a scheme alternation and treated every non-match
   as a bare path, prefixing ``sqlite:///``.  A ``mariadb://`` or ``oracle://``
   study URL therefore became a SQLite *filename* and the operator saw
   ``(sqlite3.OperationalError) unable to open database file`` for a database
   they never asked SQLite to open.

2. **The failure was unmapped (exit 1) and landed mid-training.**  It surfaced
   from inside Optuna in ``run_search``, which runs after fetch, cleansing and
   split — so the scan was already paid for — and reached the CLI as
   ``_EXIT_UNKNOWN``, which supervisor and CronJob retry logic reads as an
   unknown crash and retries forever.

The exit-code assertions go through ``_map_exception_to_exit`` rather than
asserting on the exception type, because the exit code is the contract an
operator's retry logic actually consumes.
"""

from __future__ import annotations

import pytest

from recotem._exit_codes import (
    _EXIT_CONFIG,
    _EXIT_UNKNOWN,
    _map_exception_to_exit,
)
from recotem.training._storage_url import (
    describe_storage_path,
    validate_storage_path,
)
from recotem.training.errors import TrainingError

# Spellings that must be ACCEPTED: the two always-available forms, plus every
# dialect+driver combination the installed extras really provide.
ACCEPTED = [
    "",
    "   ",
    "/var/lib/recotem/optuna.db",
    "optuna.db",
    "./relative/optuna.db",
    "C://data/optuna.db",  # Windows drive letter is a path, not a scheme
    "sqlite:///tmp/optuna.db",
    "sqlite:////abs/optuna.db",
    "postgresql+psycopg://host/db",
    "mysql+pymysql://host/db",
    # Accepted, and correctly so: `mariadb` is a supported dialect and pymysql
    # is installed, so nothing about this URL is unusable.  On a tree without
    # the #261 re-land it still fails afterwards, inside ``_make_storage``,
    # which classifies it as a filename -- but that is a separate defect with a
    # separate owner, and refusing the URL here to paper over it would be a
    # lie that has to be reverted the moment #261 lands.  This pre-flight is
    # not making that case worse: it exited 1 with a SQLite error before this
    # change and it exits 1 with a SQLite error after it.
    "mariadb+pymysql://host/db",
]

# Spellings that must be REFUSED, with the substring that makes the message
# actionable.  Every one of these reached the operator as exit 1 before.
REFUSED = [
    # --- defect 1: silently became a SQLite filename -------------------
    ("mariadb://host/db", "mariadb+pymysql://"),
    ("mariadb+pymysql://host/db".replace("pymysql", "mysqldb"), "mysqldb"),
    ("oracle://host/db", "unsupported dialect"),
    ("oracle+cx_oracle://host/db", "unsupported dialect"),
    ("mssql+pyodbc://host/db", "unsupported dialect"),
    ("cockroachdb://host/db", "unsupported dialect"),
    # --- defect 2: failed inside Optuna with an unhelpful message ------
    ("postgresql://host/db", "postgresql+psycopg://"),
    ("postgres://host/db", "removed"),
    ("mysql://host/db", "mysql+pymysql://"),
    ("postgresql+psycopg2://host/db", "psycopg2"),
]


@pytest.mark.parametrize("path", ACCEPTED)
def test_usable_storage_path_is_accepted(path: str) -> None:
    validate_storage_path(path)


@pytest.mark.parametrize(("path", "needle"), REFUSED)
def test_unusable_storage_path_is_refused(path: str, needle: str) -> None:
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path(path)
    assert excinfo.value.code == "storage_path_unusable"
    assert needle in str(excinfo.value), (
        f"message for {path!r} must name the fix ({needle!r}); got: {excinfo.value}"
    )


@pytest.mark.parametrize(("path", "_needle"), REFUSED)
def test_unusable_storage_path_exits_8_not_1(path: str, _needle: str) -> None:
    """The whole point: a broken study backend is exit 8, never exit 1.

    ``_EXIT_UNKNOWN`` is asserted against explicitly because that is the value
    every one of these produced before the fix, and a regression would land
    back on it rather than on some third code.
    """
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path(path)
    code = _map_exception_to_exit(excinfo.value)
    assert code == _EXIT_CONFIG, f"{path!r} mapped to exit {code}, want 8"
    assert code != _EXIT_UNKNOWN


def test_mariadb_url_is_never_treated_as_a_sqlite_filename() -> None:
    """The original #261 symptom, asserted on the symptom rather than the fix.

    A ``mariadb`` study URL must not reach SQLite.  Guarding the *behaviour*
    (nothing mentions sqlite) rather than the regex means this still holds if
    the classification is rewritten again.
    """
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path("mariadb://127.0.0.1:3306/recodb")
    message = str(excinfo.value).lower()
    assert "unable to open database file" not in message
    assert "sqlite" not in message, (
        "a mariadb:// storage_path must not be reported as a SQLite problem; "
        f"got: {excinfo.value}"
    )


def test_run_search_validates_before_building_storage() -> None:
    """The guard must sit on the production path, not only in this test file.

    ``run_search`` is the only production caller.  If the call is removed the
    defect returns in full — the failure moves back inside Optuna and back to
    exit 1 — while every other test here still passes, so this asserts the
    wiring directly.
    """
    import inspect

    from recotem.training import search

    source = inspect.getsource(search.run_search)
    assert "validate_storage_path(storage_path)" in source, (
        "run_search must pre-flight storage_path; without this call the "
        "failure returns to inside Optuna and to exit 1"
    )
    assert source.index("validate_storage_path(storage_path)") < source.index(
        "_make_storage(storage_path)"
    ), "the pre-flight must run BEFORE _make_storage, or it cannot prevent it"


def _recipe(tmp_path, storage_path: str, name: str = "sp"):
    """A schema-valid recipe with a real CSV, so validate can reach exit 0."""
    csv = tmp_path / f"{name}.csv"
    rows = "\n".join(
        f"u{u},i{i}" for u in range(12) for i in range(6) if (u + i) % 2 == 0
    )
    csv.write_text("user_id,item_id\n" + rows + "\n")
    yaml_path = tmp_path / f"{name}.yaml"
    yaml_path.write_text(
        f"name: {name}\n"
        "source:\n"
        "  type: csv\n"
        f"  path: {csv}\n"
        "schema:\n"
        "  user_column: user_id\n"
        "  item_column: item_id\n"
        "training:\n"
        "  algorithms: [TopPop]\n"
        "  cutoff: 3\n"
        "  n_trials: 1\n"
        f'  storage_path: "{storage_path}"\n'
        "output:\n"
        f"  path: {tmp_path / (name + '.recotem')}\n"
    )
    return yaml_path


def test_validate_rejects_unusable_storage_path(tmp_path) -> None:
    """``recotem validate`` must refuse a study backend that cannot open.

    This is the half of the fix that actually saves money: without it the
    failure only appears inside ``run_search``, which runs after the dataset has
    been fetched, cleansed and split — a billed scan on a BigQuery- or
    SQL-backed recipe.

    Asserted through the real CLI rather than by inspecting ``cli.validate``'s
    source: a source check is satisfied by the *import* line alone, so deleting
    the call while leaving the import would pass it.
    """
    from typer.testing import CliRunner

    from recotem.cli import app

    yaml_path = _recipe(tmp_path, "mariadb://127.0.0.1:3306/recodb", "bad_sp")
    result = CliRunner().invoke(app, ["validate", str(yaml_path)])

    assert result.exit_code == _EXIT_CONFIG, (
        "validate must refuse an unopenable storage_path with exit 8; got "
        f"{result.exit_code}. Output:\n{result.output}"
    )
    assert "mariadb+pymysql://" in result.output, (
        f"the refusal must name the spelling that works; got:\n{result.output}"
    )


def test_validate_accepts_and_reports_a_usable_storage_path(tmp_path) -> None:
    """Positive control: a good storage_path still validates, and is reported.

    Without this, a pre-flight that refused *everything* would pass the test
    above, and the guard would prove nothing.
    """
    from typer.testing import CliRunner

    from recotem.cli import app

    yaml_path = _recipe(tmp_path, str(tmp_path / "optuna.db"), "good_sp")
    result = CliRunner().invoke(app, ["validate", str(yaml_path)])

    assert result.exit_code == 0, f"Output:\n{result.output}"
    assert "Optuna storage: OK" in result.output


def test_validate_never_prints_storage_path_credentials(tmp_path) -> None:
    """``validate`` writes to stdout, and stdout goes into CI logs."""
    from typer.testing import CliRunner

    from recotem.cli import app

    yaml_path = _recipe(
        tmp_path, "postgresql+psycopg://user:hunter2@example.com/db", "cred_sp"
    )
    result = CliRunner().invoke(app, ["validate", str(yaml_path)])
    assert "hunter2" not in result.output, (
        f"storage_path credentials leaked to stdout:\n{result.output}"
    )


def test_describe_never_echoes_credentials() -> None:
    """``validate`` prints to stdout, so the description must be credential-free."""
    desc = describe_storage_path("postgresql+psycopg://user:hunter2@host/db")
    assert "hunter2" not in desc
    assert "user" not in desc
    assert "postgresql" in desc


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("", "in-memory, no resume"),
        ("/var/lib/optuna.db", "sqlite, local file"),
        ("sqlite:///x.db", "sqlite, driver 'pysqlite'"),
        ("postgresql+psycopg://h/d", "postgresql, driver 'psycopg'"),
    ],
)
def test_describe_storage_path(path: str, expected: str) -> None:
    assert describe_storage_path(path) == expected


# ---------------------------------------------------------------------------
# Exit 8 must mean "retrying can never succeed".
# ---------------------------------------------------------------------------

# Well-formed URLs naming a supported dialect and an installed driver, whose
# backend merely happens to be unreachable right now.  Every one of these is
# RECOVERABLE, so the pre-flight must let it through and leave the failure on
# the retryable path.  Converting them to _EXIT_CONFIG would tell a supervisor
# that a transient outage is a permanent misconfiguration and stop it retrying.
TRANSIENT = [
    "postgresql+psycopg://192.0.2.1:5432/db",  # TEST-NET-1, unroutable
    "postgresql+psycopg://127.0.0.1:19599/db",  # nothing listening
    "mysql+pymysql://127.0.0.1:19599/db",  # nothing listening
    "postgresql+psycopg://does-not-resolve.invalid/db",  # DNS failure
    "sqlite:////nonexistent-mount/optuna.db",  # network FS outage, cf. #274
    "/nonexistent-mount/optuna.db",  # same, as a bare path
]


@pytest.mark.parametrize("path", TRANSIENT)
def test_transient_backend_failures_are_not_converted_to_exit_8(path: str) -> None:
    """A backend that is merely *down* is not a configuration error.

    The discriminator for ``_EXIT_CONFIG`` is "can retrying ever succeed?", not
    "is this about storage".  A scheme no version of the software can parse is
    permanently broken; an unreachable host or a stalled network mount is not,
    and must keep whatever exit code the real failure produces so retry logic
    still fires.
    """
    validate_storage_path(path)


def test_preflight_opens_no_network_connection() -> None:
    """The pre-flight is a pure local check: parse plus ``__import__``.

    This is what makes it safe to run in ``recotem validate`` before any source
    is touched, and it is also what keeps the previous test true — a check that
    dialled the server would turn every outage into a pre-flight failure.
    """
    import socket

    real_socket = socket.socket

    class _NoConnect(real_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            raise AssertionError("storage_path pre-flight opened a connection")

        def connect_ex(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            raise AssertionError("storage_path pre-flight opened a connection")

    socket.socket = _NoConnect  # type: ignore[misc]
    try:
        for path in [*TRANSIENT, *ACCEPTED, *(p for p, _ in REFUSED)]:
            try:
                validate_storage_path(path)
            except TrainingError:
                pass  # refusals are fine; opening a socket is not
    finally:
        socket.socket = real_socket  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The refusal must name the pip extra, not just the DSN spelling.
# ---------------------------------------------------------------------------

# `sqlalchemy` reaches every install transitively via optuna, but `psycopg` and
# `pymysql` live only in the `postgres` / `mysql` extras.  So on a bare
# `pip install recotem` the *recommended* spelling parses and then fails at
# driver import — telling the operator to "write it as postgresql+psycopg://"
# is, on its own, advice they have already followed.  The message has to name
# the missing package too.
_EXTRA_BY_DSN = [
    ("postgresql://h/db", "recotem[postgres]"),
    ("postgresql+psycopg2://h/db", "recotem[postgres]"),
    ("mysql://h/db", "recotem[mysql]"),
    ("mariadb://h/db", "recotem[mysql]"),
]


@pytest.mark.parametrize(("path", "extra"), _EXTRA_BY_DSN)
def test_driver_refusal_names_the_pip_extra(path: str, extra: str) -> None:
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path(path)
    message = str(excinfo.value)
    assert extra in message, (
        f"the refusal for {path!r} must name the extra that ships the driver "
        f"({extra}); got: {message}"
    )
    assert "pip install" in message


def test_unsupported_dialect_message_mentions_the_extras() -> None:
    """The 'here are the supported forms' list must not imply they work bare."""
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path("oracle://h/db")
    message = str(excinfo.value)
    assert "recotem[postgres]" in message and "recotem[mysql]" in message, (
        "listing the supported DSN forms without saying they need a driver "
        f"extra reproduces the gap this check exists to close; got: {message}"
    )
