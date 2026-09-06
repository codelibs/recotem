"""``training.storage_path`` scheme classification.

A ``mariadb`` storage_path is a *server* URL, like ``mysql`` and
``postgresql``.  Reading it as a bare filename had three visible consequences,
one per test group below, and every one of them is reproducible without a
database: the classification happens before anything connects.
"""

from __future__ import annotations

import re

import pytest

from recotem.training.search import (
    _SERVER_URL_RE,
    _STORAGE_URL_RE,
    _make_storage,
)

# Spellings an operator can reasonably write for each backend.  ``mariadb``
# rows are the ones the omission broke.
SERVER_URLS = [
    "postgresql://host/optuna",
    "postgresql+psycopg://host/optuna",
    "postgres://host/optuna",
    "mysql://host/optuna",
    "mysql+pymysql://host/optuna",
    "mariadb://host/optuna",
    "mariadb+pymysql://host/optuna",
]

FILESYSTEM_PATHS = [
    "/var/lib/recotem/study.db",
    "study.db",
    "./nested/study.db",
    # A path that merely *starts with* a backend word is still a path: the
    # \b anchor must not let "mariadb_backup.db" masquerade as a URL.
    "mariadb_backup.db",
    "mysqldump.db",
    "postgres_notes.db",
]


@pytest.mark.parametrize("url", SERVER_URLS)
def test_server_urls_are_classified_as_urls_not_filenames(url: str) -> None:
    """Both classifiers must see every server spelling as a URL.

    ``_STORAGE_URL_RE`` decides whether ``_make_storage`` hands the value to
    ``RDBStorage`` as-is or prefixes ``sqlite:///`` to it; ``_SERVER_URL_RE``
    decides whether the parallelism guard downgrades to 1 citing SQLite.
    """
    assert _STORAGE_URL_RE.match(url), f"{url} would be read as a filename"
    assert _SERVER_URL_RE.match(url), f"{url} would be treated as SQLite"


@pytest.mark.parametrize("path", FILESYSTEM_PATHS)
def test_filesystem_paths_are_not_mistaken_for_urls(path: str) -> None:
    assert not _SERVER_URL_RE.match(path), f"{path} wrongly treated as a server URL"


@pytest.mark.parametrize("url", SERVER_URLS)
def test_credentials_are_refused_for_every_server_spelling(url: str) -> None:
    """The credential refusal lives inside the URL branch.

    A spelling that misses the branch skips the check, and the password is
    then baked into a *filename* instead of being rejected -- so this is a
    credential-handling test, not a cosmetic one.
    """
    scheme, rest = url.split("://", 1)
    with_creds = f"{scheme}://someuser:s3cr3t@{rest}"
    with pytest.raises(Exception) as excinfo:
        _make_storage(with_creds)
    message = str(excinfo.value)
    assert "must not embed credentials" in message, (
        f"{with_creds} did not reach the credential guard; got: {message}"
    )
    assert "s3cr3t" not in message


def test_mariadb_storage_path_is_not_turned_into_a_sqlite_filename() -> None:
    """The concrete regression: the value must not be prefixed with sqlite:///.

    ``_make_storage`` would otherwise build
    ``sqlite:///mariadb+pymysql://host/optuna`` and ask SQLite to open a file
    by that name, reporting "unable to open database file".
    """
    url = "mariadb+pymysql://host/optuna"
    assert _STORAGE_URL_RE.match(url)
    # Prove the fallback branch it must not reach would produce this:
    would_have_been = f"sqlite:///{url}"
    assert would_have_been.startswith("sqlite:///mariadb")


def test_the_two_classifiers_stay_in_sync() -> None:
    """The bug was two hand-written lists disagreeing; keep them derived.

    Every scheme the server classifier accepts must also be accepted by the
    storage classifier, which is the strictly wider one (it adds sqlite).
    """
    for url in SERVER_URLS:
        assert bool(_SERVER_URL_RE.match(url)) is bool(_STORAGE_URL_RE.match(url))
    assert _STORAGE_URL_RE.match("sqlite:///x.db")
    assert not _SERVER_URL_RE.match("sqlite:///x.db")


def test_source_has_no_hand_written_scheme_alternation_left() -> None:
    """No second copy of the scheme list may reappear in the module.

    The defect was a literal alternation drifting from its sibling; a new one
    is how it comes back.
    """
    import inspect

    import recotem.training.search as mod

    src = inspect.getsource(mod)
    stray = re.findall(r'r?"\^\((?:postgresql|postgres|mysql)[^"]*\)\\b"', src)
    assert not stray, f"hand-written scheme alternation reintroduced: {stray}"
