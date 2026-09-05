"""Pre-flight validation for ``training.storage_path``.

``training.storage_path`` is handed straight to ``optuna.storages.RDBStorage``,
which has no driver pre-flight of its own.  Two consequences, both of which
this module exists to remove:

1. **An unrecognised URL scheme is read as a filename.**  ``_make_storage``
   classifies the value with a scheme alternation and treats everything that
   does not match as a bare path, prefixing ``sqlite:///``.  So
   ``mariadb+pymysql://host/db`` became the SQLite *filename*
   ``mariadb+pymysql://host/db`` and the operator saw
   ``(sqlite3.OperationalError) unable to open database file`` for a database
   they never asked SQLite to open.  Adding ``mariadb`` to the alternation
   (#261) fixes that one scheme; it does not fix the shape.  ``oracle://`` and
   every other unsupported scheme still silently become filenames.  This module
   inverts the rule: a value that *looks like a URL* must name a supported
   dialect, and only a value that does not look like a URL is a path.

2. **The failure lands mid-training, after the data is billed.**  ``run_search``
   runs after fetch, cleansing and split, so a BigQuery- or SQL-backed recipe
   pays for the whole scan and only then discovers that the study backend was
   never going to open.  ``recotem validate`` -- the documented pre-flight gate
   -- never read the field at all and printed ``Validation passed.``

The driver table is imported from ``recotem.datasource.sql`` rather than
restated here.  Two hand-maintained copies of a driver list drifting apart is
precisely the defect #261 fixed inside ``search.py``; reintroducing a third
copy one module over would be the same mistake with a wider blast radius.
``training/`` already imports from ``datasource/`` (``pipeline.py``,
``features.py``), so this adds no new dependency edge.

Failures raise ``TrainingError(code="storage_path_unusable")``, which
``_map_exception_to_exit`` maps to ``_EXIT_CONFIG`` (8).  Exit 8 rather than
exit 2 because the failure is *environmental*, not textual: the identical
recipe is valid on a host with ``recotem[postgres]`` installed and invalid on
one without.  That is the rule the neighbouring ``output.path`` codes
(``artifact_write_credentials`` / ``artifact_write_destination``) already
follow -- a recipe path field whose failure depends on the deployment reports
8, while its purely textual failures (rejected scheme, ``RECOTEM_ARTIFACT_ROOT``
escape) report 2.  Exit 3 would be wrong for a different reason: ``storage_path``
is the Optuna study backend, not a data source, and reporting 3 would tell a
supervisor the data source is broken when it is not.
"""

from __future__ import annotations

import re

from recotem.training.errors import TrainingError

# A value is treated as a URL only when it carries a ``<scheme>://`` prefix
# whose scheme is at least two characters.  The two-character floor keeps a
# Windows drive letter (``C://data/optuna.db``) on the filename path, where it
# belongs; every dialect name recotem supports is far longer.
#
# The underscore is load-bearing.  A SQLAlchemy scheme is ``dialect+driver``
# and driver names contain underscores -- ``cx_oracle``, ``pysqlite_numeric``,
# ``mysqlconnector``.  Omitting it makes the pattern stop at the underscore, so
# ``oracle+cx_oracle://host/db`` fails to look like a URL and falls through to
# the filename branch -- reintroducing, for exactly the spellings that carry a
# driver suffix, the same "URL silently becomes a SQLite filename" defect this
# module exists to remove.
_URL_SHAPED = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-_]+://")

_CODE = "storage_path_unusable"


def _fail(message: str) -> TrainingError:
    return TrainingError(message, code=_CODE)


def describe_storage_path(storage_path: str) -> str:
    """Return a credential-free one-line description of *storage_path*.

    ``recotem validate`` writes to stdout, and a study URL may carry userinfo
    (``_make_storage`` refuses such a URL, but only at train time, so validate
    can still be handed one).  Echoing the value would put a password on the
    terminal and into any CI log that captures it, so only the dialect and
    driver -- neither of which can contain a credential -- are reported.

    Assumes :func:`validate_storage_path` has already accepted the value.
    """
    path = storage_path.strip()
    if not path:
        return "in-memory, no resume"
    if not _URL_SHAPED.match(path):
        return "sqlite, local file"

    from sqlalchemy.engine.url import make_url  # noqa: PLC0415

    url = make_url(path)
    driver = url.get_driver_name()
    return f"{url.get_backend_name()}, driver {driver!r}"


def validate_storage_path(storage_path: str) -> None:
    """Raise ``TrainingError`` if *storage_path* cannot open a study backend.

    A no-op for the two forms that always work: the empty string (in-memory
    storage) and a bare filesystem path (which ``_make_storage`` turns into a
    SQLite URL, and SQLite's driver is the standard library).

    Checks performed on a URL-shaped value, in the order an operator hits them:

    * the URL parses at all;
    * the dialect is one recotem supports as a study backend;
    * the dialect is not one SQLAlchemy 2.x removed (``postgres://``);
    * the DBAPI the URL actually routes to is importable on this host.

    The last check is the one that matters most and the one Optuna does not
    do.  A bare ``postgresql://`` routes to SQLAlchemy's *default* PostgreSQL
    DBAPI -- psycopg2, which recotem does not install -- so it fails inside
    Optuna with ``ImportError: Failed to import DB access module for the
    specified storage URL``, naming neither the module nor the fix.
    """
    from sqlalchemy.engine.url import make_url  # noqa: PLC0415
    from sqlalchemy.exc import ArgumentError  # noqa: PLC0415

    from recotem.datasource.sql import (  # noqa: PLC0415
        _BACKEND_RECOMMENDED_DSN,
        _DRIVER_MODULE,
        _REMOVED_DIALECT_ALIASES,
    )

    path = storage_path.strip()
    if not path:
        return  # in-memory storage
    if not _URL_SHAPED.match(path):
        return  # bare filesystem path -> sqlite:///<path>, always available

    try:
        url = make_url(path)
    except (ArgumentError, ValueError, TypeError) as exc:
        # ``str(exc)`` is safe here: SQLAlchemy's parse failure is
        # "Could not parse SQLAlchemy URL from given URL string" and does not
        # echo the input.  It is omitted anyway -- the field name is the useful
        # half, and the value may carry credentials.
        raise _fail(
            "training.storage_path is not a valid SQLAlchemy URL. Use a bare "
            "filesystem path for SQLite, or one of: "
            f"{sorted(_BACKEND_RECOMMENDED_DSN.values())}."
        ) from exc

    backend = url.get_backend_name()

    replacement = _REMOVED_DIALECT_ALIASES.get(backend)
    if replacement is not None:
        raise _fail(
            f"training.storage_path uses dialect {backend!r}, which SQLAlchemy "
            "2.x removed; no +driver suffix can load it. Use "
            f"{_BACKEND_RECOMMENDED_DSN[replacement]} instead."
        )

    if backend not in _BACKEND_RECOMMENDED_DSN:
        raise _fail(
            f"training.storage_path uses unsupported dialect {backend!r}. "
            "Supported study backends are a bare filesystem path (SQLite) or "
            f"one of: {sorted(_BACKEND_RECOMMENDED_DSN.values())}. Note that "
            "an unsupported scheme is NOT treated as a filename."
        )

    driver = url.get_driver_name()
    if driver not in _DRIVER_MODULE:
        raise _fail(
            f"training.storage_path names unknown driver {driver!r} for "
            f"dialect {backend!r}. recotem probes a fixed set of drivers and "
            "will not import a name supplied by the recipe. Known drivers: "
            f"{sorted(_DRIVER_MODULE)}. Write it as "
            f"{_BACKEND_RECOMMENDED_DSN[backend]} instead."
        )

    driver_mod = _DRIVER_MODULE[driver]
    if driver_mod is None:
        return  # stdlib sqlite3

    try:
        __import__(driver_mod)
    except ImportError as exc:
        explicit = "+" in url.drivername
        detail = (
            f"training.storage_path names driver {driver!r} explicitly"
            if explicit
            else (
                f"{backend}:// with no +driver suffix defaults to {driver!r}, "
                "which recotem does not install"
            )
        )
        raise _fail(
            f"cannot load the {driver!r} driver for training.storage_path "
            f"dialect {backend!r}: {detail}. Write it as "
            f"{_BACKEND_RECOMMENDED_DSN[backend]}, or install {driver_mod!r} "
            "yourself."
        ) from exc
