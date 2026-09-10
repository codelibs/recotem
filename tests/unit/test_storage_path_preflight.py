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

_SIGNING = "dev:" + "ab" * 32

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


# ---------------------------------------------------------------------------
# The residual class: the driver loads, the backend does not open.
#
# ``validate_storage_path`` answers everything decidable from text plus a local
# import.  Whether the backend *opens* -- a directory that exists, a permission
# bit, a running server -- it deliberately does not touch, because
# ``recotem validate`` has to stay a text-and-driver check so a lint job on one
# host can vet a recipe that trains on another.  (``output.path``, the other
# write target in a recipe, is handled the same way: validate does not probe it,
# and an unwritable destination reports 8 at train time.)
#
# So that class lands in ``run_search``, and it landed there as exit 1.
# Measured on 08b1672, after fetch / cleanse / split had already run:
#
#   /no/such/dir/study.db        -> exit 1  (sqlite3.OperationalError)
#   <a directory>                -> exit 1  (sqlite3.OperationalError)
#   <a read-only directory>      -> exit 1  (sqlite3.OperationalError)
#   postgresql+psycopg://<down>  -> exit 1  (psycopg.OperationalError)
#
# Exit 1 is "unhandled exception", so a CronJob retry policy reads a recipe
# typo as a recotem crash. Only the SQLite spellings are exercised here: they
# are deterministic and need no network.
# ---------------------------------------------------------------------------


def _trainable_recipe(tmp_path, storage_path: str, name: str):
    """Like ``_recipe`` but with per-user depth enough for the split to run.

    ``_recipe`` exists for ``validate``, which never splits; its 12 users have
    3 items each and ``train`` on it exits 4 with a split error before the
    study backend is ever built.  These tests need ``train`` to reach
    ``run_search``, so every user gets 12 items.
    """
    csv = tmp_path / f"{name}.csv"
    rows = "\n".join(f"u{u},i{i}" for u in range(20) for i in range(12))
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


@pytest.mark.parametrize(
    ("subpath", "label"),
    [
        ("missing_dir/study.db", "a directory that does not exist"),
        ("", "a path that is a directory"),
    ],
)
def test_train_exits_8_when_the_sqlite_study_file_cannot_open(
    tmp_path, subpath: str, label: str
) -> None:
    """An unopenable study file is a config error (8), not a crash (1)."""
    from typer.testing import CliRunner

    from recotem.cli import app

    storage = str(tmp_path / subpath) if subpath else str(tmp_path)
    yaml_path = _trainable_recipe(tmp_path, storage, "unopenable")
    result = CliRunner().invoke(
        app,
        ["train", str(yaml_path)],
        env={"RECOTEM_SIGNING_KEYS": _SIGNING},
    )

    assert result.exit_code != _EXIT_UNKNOWN, (
        f"{label} must not report as an unhandled exception; "
        f"got exit 1. Output:\n{result.output}"
    )
    assert result.exit_code == _EXIT_CONFIG, (
        f"{label} must report exit 8; got {result.exit_code}. Output:\n{result.output}"
    )
    assert "training.storage_path" in result.output, (
        "the failure must name the recipe field the operator has to fix; "
        f"got:\n{result.output}"
    )


def test_train_succeeds_with_a_writable_study_file(tmp_path) -> None:
    """Positive control: the same recipe with an openable path still trains.

    Without this, a wrapper that turned *every* ``_make_storage`` outcome into
    exit 8 would pass the test above.
    """
    from typer.testing import CliRunner

    from recotem.cli import app

    yaml_path = _trainable_recipe(tmp_path, str(tmp_path / "study.db"), "openable")
    result = CliRunner().invoke(
        app,
        ["train", str(yaml_path)],
        env={"RECOTEM_SIGNING_KEYS": _SIGNING},
    )

    assert result.exit_code == 0, f"Output:\n{result.output}"
    assert (tmp_path / "study.db").exists(), (
        "the study file must actually have been created"
    )


# ---------------------------------------------------------------------------
# Userinfo: refused before the scan, and described accurately.
#
# ``_make_storage`` has always refused a study URL carrying userinfo, but only
# from inside ``run_search`` -- after fetch, cleansing and split.  Measured on
# 08b1672: ``recotem validate`` printed ``Validation passed.`` for
# ``postgresql+psycopg://recotem@127.0.0.1:19501/recotem`` and ``train`` then
# exited 4.
#
# The refusal also covers a *bare username*, which nothing said.  Every existing
# test uses ``user:pass@`` and the shipped message names ``(user:pass@host)``.
# An operator following the documented ~/.pgpass route writes exactly the
# username-only form, because ~/.pgpass matches on user.
#
# Verified against live servers while writing this: PostgreSQL works end to end
# with PGUSER + PGPASSFILE and no userinfo in the DSN; MariaDB 11.8.9 works only
# when the server knows the OS account, because pymysql reads no user variable.
# That is why the remedy is dialect-specific.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expect_word"),
    [
        ("postgresql+psycopg://recotem@host:5432/db", "a username"),
        ("postgresql+psycopg://recotem:hunter2@host:5432/db", "a password"),
        ("mariadb+pymysql://optuna@host:3306/db", "a username"),
        ("mysql+pymysql://optuna:hunter2@host:3306/db", "a password"),
    ],
)
def test_userinfo_is_refused_and_named_precisely(url: str, expect_word: str) -> None:
    """A username-only URL must not be reported as an embedded password."""
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path(url)

    message = str(excinfo.value)
    assert excinfo.value.code == "storage_path_unusable"
    assert expect_word in message, (
        f"{url!r} must be described as embedding {expect_word}; got: {message}"
    )
    wrong = "a password" if expect_word == "a username" else "a username"
    assert wrong not in message, (
        f"{url!r} must not be described as embedding {wrong}; got: {message}"
    )
    assert "hunter2" not in message and "recotem@" not in message, (
        f"the refusal must not echo the value; got: {message}"
    )


@pytest.mark.parametrize(
    ("url", "needle"),
    [
        ("postgresql+psycopg://recotem@host/db", "PGUSER"),
        ("mariadb+pymysql://optuna@host/db", "OS account"),
        ("mysql+pymysql://optuna@host/db", "OS account"),
    ],
)
def test_userinfo_refusal_names_the_remedy_for_that_dialect(
    url: str, needle: str
) -> None:
    """Generic "use env-driven auth" advice has no MySQL spelling.

    libpq reads PGUSER; pymysql reads nothing, so the only thing to point a
    MySQL / MariaDB operator at is the OS account the process runs as.
    """
    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path(url)
    assert needle in str(excinfo.value), (
        f"{url!r} must name {needle!r} as the way through; got: {excinfo.value}"
    )


def test_validate_refuses_userinfo_before_any_data_is_fetched(tmp_path) -> None:
    """The point of moving the check: ``validate`` catches it, not ``train``.

    Before this, validate exited 0 on the same recipe and the refusal arrived
    from inside ``run_search``, after the scan had been paid for.
    """
    from typer.testing import CliRunner

    from recotem.cli import app

    yaml_path = _recipe(tmp_path, "postgresql+psycopg://recotem@h/db", "userinfo_sp")
    result = CliRunner().invoke(app, ["validate", str(yaml_path)])

    assert result.exit_code == _EXIT_CONFIG, (
        "validate must refuse a storage_path carrying userinfo with exit 8; "
        f"got {result.exit_code}. Output:\n{result.output}"
    )
    assert "Validation passed" not in result.output
    assert "recotem@" not in result.output, (
        f"validate must not echo the userinfo; got:\n{result.output}"
    )


def test_userinfo_free_server_url_still_validates(tmp_path) -> None:
    """Positive control: the supported spelling is untouched.

    Without this, a check that refused every server URL would pass the tests
    above. Exercised end to end against a live PostgreSQL server separately;
    here it only has to reach exit 0, since psycopg is installed.
    """
    from typer.testing import CliRunner

    from recotem.cli import app

    yaml_path = _recipe(tmp_path, "postgresql+psycopg://h:5432/db", "nouserinfo_sp")
    result = CliRunner().invoke(app, ["validate", str(yaml_path)])

    assert result.exit_code == 0, f"Output:\n{result.output}"
    assert "Optuna storage: OK" in result.output


# ---------------------------------------------------------------------------
# A driver that is installed but broken.
#
# The import probe caught only ImportError, so a driver whose own top-level code
# raises anything else -- psycopg against a mismatched libpq, a DBAPI whose C
# accelerator fails to initialise, a package refusing an unsupported platform --
# escaped the pre-flight and reached the CLI as exit 1 carrying the driver's
# text and nothing else.
#
# Measured on 08b1672 in a bare `pip install recotem` venv, same DSN, only the
# driver's failure mode changed:
#
#   psycopg absent (ImportError)         -> validate 8 / train 8, names the
#                                           field, the driver and the extra
#   psycopg present, raises RuntimeError -> validate 1 / train 1,
#                                           "BROKEN-DRIVER: libpq version mismatch"
#
# The operator whose install is broken is the one who most needs the field
# named, so the two cannot differ like that.
# ---------------------------------------------------------------------------


def test_broken_driver_is_reported_as_a_storage_path_failure(monkeypatch) -> None:
    """A non-ImportError from the driver must still be exit 8, not exit 1."""
    import builtins

    real_import = builtins.__import__

    def _explode(name, *args, **kwargs):
        if name == "psycopg":
            raise RuntimeError("libpq version mismatch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _explode)

    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path("postgresql+psycopg://host:5432/db")

    assert excinfo.value.code == "storage_path_unusable"
    code = _map_exception_to_exit(excinfo.value)
    assert code == _EXIT_CONFIG, f"mapped to exit {code}, want 8"
    assert code != _EXIT_UNKNOWN

    message = str(excinfo.value)
    assert "training.storage_path" in message, (
        f"the failure must name the recipe field; got: {message}"
    )
    assert "installed but failed to import" in message, (
        f"a broken install must be distinguished from a missing one; got: {message}"
    )
    assert "RuntimeError" in message and "libpq version mismatch" in message, (
        f"the driver's own diagnosis must survive; got: {message}"
    )


def test_missing_driver_still_says_missing_not_broken(monkeypatch) -> None:
    """Positive control: the absent case keeps its own wording and its extra.

    Without this, collapsing both arms into one "failed to import" message
    would pass the test above while losing `pip install recotem[postgres]`,
    which is the only actionable half for the far more common case.
    """
    import builtins

    real_import = builtins.__import__

    def _absent(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("No module named 'psycopg'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _absent)

    with pytest.raises(TrainingError) as excinfo:
        validate_storage_path("postgresql+psycopg://host:5432/db")

    message = str(excinfo.value)
    assert "recotem[postgres]" in message, (
        f"a missing driver must still name the extra; got: {message}"
    )
    assert "installed but failed to import" not in message, (
        f"an absent driver must not be described as broken; got: {message}"
    )
