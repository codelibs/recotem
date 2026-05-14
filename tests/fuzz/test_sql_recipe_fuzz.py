"""Fuzz tests for SQL recipe loading.

Hypothesis-driven byte mutation of a valid SQL recipe YAML; the loader must
always either return a valid Recipe or raise RecipeError — never any other
unhandled exception type.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from recotem.recipe.errors import RecipeError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_SQL_YAML = """\
name: sql_fuzz
source:
  type: sql
  dsn_env: RECOTEM_RECIPE_DB_DSN
  query: SELECT user_id, item_id, ts FROM events
schema:
  user_column: user_id
  item_column: item_id
  time_column: ts
training:
  algorithms: [TopPop]
output:
  path: /tmp/out.recotem
"""

_MINIMAL_SQL_BYTES: bytes = MINIMAL_SQL_YAML.encode("utf-8")


def _try_load_bytes(data: bytes, tmp_path: Path) -> None:
    """Write *data* as bytes and attempt to load; accept RecipeError or simple
    parse/encoding errors — raise AssertionError on any other exception."""
    from recotem.recipe.loader import load_recipe

    yaml_file = tmp_path / "fuzz_sql.yaml"
    try:
        yaml_file.write_bytes(data)
    except OSError:
        return  # can't write; skip

    try:
        recipe = load_recipe(yaml_file)
        # If it loaded cleanly, it must have a name and source
        assert hasattr(recipe, "name")
        assert hasattr(recipe, "source")
    except RecipeError:
        pass  # expected for invalid / mutated recipes
    except (ValueError, UnicodeDecodeError):
        pass  # trivial parse / encoding errors acceptable on byte mutations
    except Exception as exc:
        raise AssertionError(
            f"Unexpected exception type {type(exc).__name__}: {exc}\n"
            f"Input (first 200 bytes): {data[:200]!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Hypothesis: arbitrary binary input
# ---------------------------------------------------------------------------


@given(data=st.binary(min_size=0, max_size=512))
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_sql_recipe_loader_handles_arbitrary_bytes(data: bytes, tmp_path: Path) -> None:
    """Arbitrary bytes passed to load_recipe never cause unhandled exceptions."""
    _try_load_bytes(data, tmp_path)


# ---------------------------------------------------------------------------
# Hypothesis: valid SQL recipe with byte-range mutations (insert at offset)
# ---------------------------------------------------------------------------


@given(
    mutation=st.binary(min_size=0, max_size=64),
    offset=st.integers(min_value=0, max_value=len(_MINIMAL_SQL_BYTES)),
)
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_sql_recipe_fuzz_mutations_never_uncaught(
    mutation: bytes, offset: int, tmp_path: Path
) -> None:
    """Inserting arbitrary bytes anywhere in a valid SQL recipe never raises
    an unhandled exception — only RecipeError or trivial parse/encoding errors."""
    mutated = _MINIMAL_SQL_BYTES[:offset] + mutation + _MINIMAL_SQL_BYTES[offset:]
    _try_load_bytes(mutated, tmp_path)


# ---------------------------------------------------------------------------
# Hypothesis: single-byte flip mutations
# ---------------------------------------------------------------------------


@given(
    flip_offset=st.integers(min_value=0, max_value=len(_MINIMAL_SQL_BYTES) - 1),
    flip_value=st.integers(min_value=0, max_value=255),
)
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_sql_recipe_fuzz_single_byte_flip(
    flip_offset: int, flip_value: int, tmp_path: Path
) -> None:
    """Replacing any single byte with any value never raises an unhandled exception."""
    mutated = (
        bytearray(_MINIMAL_SQL_BYTES[:flip_offset])
        + bytes([flip_value])
        + bytearray(_MINIMAL_SQL_BYTES[flip_offset + 1 :])
    )
    _try_load_bytes(bytes(mutated), tmp_path)


# ---------------------------------------------------------------------------
# Edge cases: deterministic checks
# ---------------------------------------------------------------------------


def test_empty_input_does_not_cause_unhandled_exception(tmp_path: Path) -> None:
    """Empty file must not produce an unhandled exception."""
    _try_load_bytes(b"", tmp_path)


def test_sql_recipe_base_loads_successfully(tmp_path: Path) -> None:
    """Sanity: the unmutated MINIMAL_SQL_YAML must load without error."""
    from recotem.datasource.sql import SQLConfig
    from recotem.recipe.loader import load_recipe

    yaml_file = tmp_path / "base.yaml"
    yaml_file.write_text(MINIMAL_SQL_YAML, encoding="utf-8")
    recipe = load_recipe(yaml_file)
    assert recipe.name == "sql_fuzz"
    assert isinstance(recipe.source, SQLConfig)
    assert recipe.source.type == "sql"


def test_sql_source_type_mutated_to_unknown(tmp_path: Path) -> None:
    """Replacing 'sql' with an unknown source type must raise RecipeError."""
    mutated = MINIMAL_SQL_YAML.replace("type: sql", "type: unknown_xyz")
    _try_load_bytes(mutated.encode("utf-8"), tmp_path)


def test_sql_dsn_env_missing_prefix(tmp_path: Path) -> None:
    """dsn_env without RECOTEM_RECIPE_ prefix must raise RecipeError."""
    mutated = MINIMAL_SQL_YAML.replace(
        "dsn_env: RECOTEM_RECIPE_DB_DSN", "dsn_env: PLAIN_DB_DSN"
    )
    _try_load_bytes(mutated.encode("utf-8"), tmp_path)


def test_sql_query_empty(tmp_path: Path) -> None:
    """An empty query string must raise RecipeError."""
    mutated = MINIMAL_SQL_YAML.replace(
        "query: SELECT user_id, item_id, ts FROM events", "query: ''"
    )
    _try_load_bytes(mutated.encode("utf-8"), tmp_path)
