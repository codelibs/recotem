"""The `storage_path` docs must name the extra each server-backed form needs.

`training.storage_path` accepts `postgresql+psycopg://` and `mysql+pymysql://`,
and both docs said so without saying that a bare `pip install recotem` cannot
use either.

The gap is easy to miss precisely because the URL still *parses*: `sqlalchemy`
reaches every install transitively (Optuna depends on it and Optuna is a core
dependency), while `psycopg` and `pymysql` ship only in the `postgres` /
`mysql` extras. So on a bare install the **recommended** spelling fails with
the same `ImportError: Failed to import DB access module for the specified
storage URL` that a **wrong** spelling produces — and an operator who has
already written the `+driver` suffix has no way to tell those two apart from
the message. Documentation that fixes the spelling and stops there leaves them
exactly one step short.

Rather than restate which extra ships which driver, the assertions read it out
of `pyproject.toml`. A future release that moves `psycopg` into the base
dependencies, or renames an extra, makes these fail instead of leaving the docs
quietly wrong.

Scope note: these assert that the prerequisite is *documented and explained*,
not that any particular sentence survives. Mutations that delete a clarifying
clause while leaving the extra named and the transitive explanation intact are
deliberately not caught -- pinning exact prose would fail on any honest reword.
An attempt to also assert "the passage calls the extra required" was removed
after measurement: the passage already contains the word "required" (about the
`+driver` suffix), so the check passed on pre-existing text and asserted
nothing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _ROOT / "pyproject.toml"
_OPERATIONS = _ROOT / "docs" / "operations.md"
_RECIPE_REF = _ROOT / "docs" / "recipe-reference.md"

# (driver distribution, the extra that ships it, the DSN form that needs it)
_DRIVER_EXTRAS = [
    ("psycopg", "postgres", "postgresql+psycopg://"),
    ("pymysql", "mysql", "mysql+pymysql://"),
]


def _extras() -> dict[str, list[str]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def _base_dependencies() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


# ---------------------------------------------------------------------------
# The packaging facts the documentation depends on.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("driver", "extra", "_dsn"), _DRIVER_EXTRAS)
def test_driver_ships_only_in_its_extra(driver: str, extra: str, _dsn: str) -> None:
    """If this ever stops being true, the docs below are wrong and should fail."""
    extras = _extras()
    assert any(driver in dep for dep in extras[extra]), (
        f"{driver!r} is no longer in the {extra!r} extra"
    )
    base = " ".join(_base_dependencies())
    assert driver not in base, (
        f"{driver!r} is now a base dependency — the documentation telling "
        f"operators to install recotem[{extra}] is now misleading and should "
        "be revisited rather than left in place"
    )


def test_sqlalchemy_is_not_a_base_dependency_but_arrives_via_optuna() -> None:
    """The fact that makes the failure confusing, pinned.

    The URL parses on a bare install because Optuna pulls SQLAlchemy in. If
    SQLAlchemy ever became a direct dependency, or Optuna dropped it, the
    docs' explanation of *why* the error is misleading would stop matching.
    """
    base = " ".join(_base_dependencies())
    assert "sqlalchemy" not in base.lower(), (
        "sqlalchemy is now a direct dependency; the docs explain the confusing "
        "failure by saying it arrives transitively via Optuna"
    )
    assert "optuna" in base.lower()

    from importlib.metadata import requires

    optuna_requires = " ".join(requires("optuna") or [])
    assert "sqlalchemy" in optuna_requires.lower(), (
        "Optuna no longer depends on SQLAlchemy, so a bare install would fail "
        "earlier and differently than the docs describe"
    )


# ---------------------------------------------------------------------------
# The documentation.
# ---------------------------------------------------------------------------


def _storage_path_section(doc: Path) -> str:
    """Return only the passage that documents ``training.storage_path``.

    Assertions are scoped to this passage, never to the whole file. Both of
    these documents mention ``recotem[mysql]`` and quote the ``Failed to import
    DB access module`` error *elsewhere* — in the SQL data-source material — so
    a file-wide ``in`` check passes even when the storage_path passage has lost
    the text entirely. Measured: two mutations that stripped the prerequisite
    from the storage_path passage left a file-wide version of this test green.
    """
    text = doc.read_text(encoding="utf-8")
    if doc.name == "recipe-reference.md":
        # One table row, self-contained.
        rows = [ln for ln in text.splitlines() if ln.startswith("| `storage_path`")]
        assert len(rows) == 1, (
            f"expected exactly one storage_path table row, found {len(rows)}; "
            "the anchor is no longer unique so a scoped assertion cannot be "
            "trusted"
        )
        return rows[0]
    # operations.md: the paragraph block, up to the next heading.
    marker = "set `training.storage_path` in the recipe"
    assert text.count(marker) == 1, (
        f"expected exactly one storage_path passage in {doc.name}, found "
        f"{text.count(marker)}; the anchor is no longer unique"
    )
    start = text.index(marker)
    nxt = text.find("\n## ", start)
    return text[start : nxt if nxt != -1 else len(text)]


@pytest.mark.parametrize("doc", [_OPERATIONS, _RECIPE_REF])
@pytest.mark.parametrize(("_driver", "extra", "dsn"), _DRIVER_EXTRAS)
def test_doc_names_the_extra_next_to_the_dsn_form(
    doc: Path, _driver: str, extra: str, dsn: str
) -> None:
    section = _storage_path_section(doc)
    assert dsn in section, f"{doc.name} no longer documents {dsn}"
    assert f"recotem[{extra}]" in section, (
        f"{doc.name}'s storage_path passage documents {dsn} without naming "
        f"recotem[{extra}], the package that ships its driver — the gap this "
        "test exists to close"
    )


@pytest.mark.parametrize("doc", [_OPERATIONS, _RECIPE_REF])
def test_doc_explains_why_the_url_parses_without_the_extra(doc: Path) -> None:
    """Naming the extra is not enough; the confusing part needs saying.

    Without this, the docs list a prerequisite and leave the operator to
    discover on their own that the *recommended* spelling produces the same
    error as a *wrong* one.
    """
    section = _storage_path_section(doc).lower()
    assert "transitive" in section, (
        f"{doc.name}'s storage_path passage should explain that sqlalchemy "
        "arrives transitively, which is why the URL parses and the failure "
        "lands later"
    )
    assert "failed to import db access module" in section, (
        f"{doc.name}'s storage_path passage should quote the actual error an "
        "operator will see, since a missing extra and a wrong spelling produce "
        "the same one"
    )
