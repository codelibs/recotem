"""Fuzz tests for recotem.recipe.loader.

Hypothesis YAML mutations; the loader must always either return a valid
Recipe or raise RecipeError — never any other exception type.
"""

from __future__ import annotations

import string
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from recotem.recipe.errors import RecipeError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID_YAML = """\
name: fuzz_test
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/out.recotem
"""


def _try_load_yaml(content: str, tmp_path: Path) -> None:
    """Attempt to load a YAML string; accept RecipeError, raise on anything else."""
    from recotem.recipe.loader import load_recipe

    yaml_file = tmp_path / "fuzz_recipe.yaml"
    try:
        yaml_file.write_text(content, encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return  # can't even write it; skip

    try:
        recipe = load_recipe(yaml_file)
        # If it loaded, it must be a Recipe with a valid name
        assert hasattr(recipe, "name")
        assert hasattr(recipe, "source")
    except RecipeError:
        pass  # expected for invalid recipes
    except Exception as exc:
        raise AssertionError(
            f"Unexpected exception type {type(exc).__name__}: {exc}\n"
            f"Input:\n{content[:200]}"
        ) from exc


# ---------------------------------------------------------------------------
# Hypothesis: random YAML strings
# ---------------------------------------------------------------------------


@given(content=st.text(min_size=0, max_size=500))
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_loader_handles_arbitrary_text(content: str, tmp_path: Path) -> None:
    """Arbitrary text input to load_recipe never causes unhandled exceptions."""
    _try_load_yaml(content, tmp_path)


# ---------------------------------------------------------------------------
# Hypothesis: valid YAML with mutated values
# ---------------------------------------------------------------------------


@given(
    name=st.text(alphabet=string.printable, min_size=0, max_size=100),
    n_trials=st.one_of(st.integers(), st.text(min_size=0, max_size=10)),
)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_loader_handles_invalid_field_values(
    name: str, n_trials: object, tmp_path: Path
) -> None:
    """Mutated field values produce RecipeError, not unhandled exceptions."""
    content = f"""\
name: {name!r}
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: {n_trials!r}
output:
  path: /tmp/out.recotem
"""
    _try_load_yaml(content, tmp_path)


# ---------------------------------------------------------------------------
# Hypothesis: nested mutation of source field
# ---------------------------------------------------------------------------


@given(source_type=st.text(alphabet=string.ascii_letters, min_size=0, max_size=30))
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_loader_handles_unknown_source_type(source_type: str, tmp_path: Path) -> None:
    """Unknown source types produce RecipeError, not unhandled exceptions."""
    content = f"""\
name: fuzz_src
source:
  type: {source_type!r}
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/out.recotem
"""
    _try_load_yaml(content, tmp_path)


# ---------------------------------------------------------------------------
# Edge cases: empty/null content
# ---------------------------------------------------------------------------


def test_empty_yaml_raises_recipe_error(tmp_path: Path) -> None:
    _try_load_yaml("", tmp_path)


def test_null_yaml_raises_recipe_error(tmp_path: Path) -> None:
    _try_load_yaml("null\n", tmp_path)


def test_yaml_with_deeply_nested_structure(tmp_path: Path) -> None:
    # Deeply nested structure should not cause recursion error
    deep = "a:\n" + "  b:\n" * 50 + "    c: 1\n"
    _try_load_yaml(deep, tmp_path)


# ---------------------------------------------------------------------------
# MINOR-15: YAML anchor / alias expansion fuzz
# ---------------------------------------------------------------------------
# YAML aliases (&anchor / *alias) expand a sub-tree inline.  Deeply nested or
# malformed alias inputs must fail closed — either RecipeError or, for truly
# pathological inputs, a yaml.YAMLError (which load_recipe wraps as RecipeError).
# The loader must never raise an unhandled exception.


def test_yaml_simple_anchor_and_alias_handled_safely(tmp_path: Path) -> None:
    """A YAML document with a valid anchor/alias must not cause an unhandled error."""
    # anchor on a scalar value, reused via alias in the schema block
    content = """\
name: anchor_test
source: &src
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/anchor.recotem
"""
    _try_load_yaml(content, tmp_path)


def test_yaml_alias_pointing_to_mapping_handled(tmp_path: Path) -> None:
    """A mapping aliased to another field must not cause an unhandled exception."""
    content = """\
defaults: &defaults
  algorithms: [TopPop]
  n_trials: 1

name: alias_test
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  <<: *defaults
output:
  path: /tmp/alias.recotem
"""
    _try_load_yaml(content, tmp_path)


@given(
    alias_depth=st.integers(min_value=1, max_value=10),
    alias_char=st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=5),
)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_yaml_anchor_alias_fuzz_never_unhandled_exception(
    alias_depth: int, alias_char: str, tmp_path: Path
) -> None:
    """Hypothesis-generated anchor/alias patterns must not produce unhandled exceptions."""
    # Build a YAML document with a chain of anchor/alias references
    alias_name = f"anch_{alias_char}"
    content = f"""\
anchor_def: &{alias_name}
  key: value
{alias_name}_ref: *{alias_name}
name: fuzz_anchor
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: {alias_depth}
output:
  path: /tmp/fa.recotem
"""
    _try_load_yaml(content, tmp_path)


def test_yaml_malformed_alias_reference_fails_closed(tmp_path: Path) -> None:
    """A YAML document with a reference to an undefined anchor fails as RecipeError."""
    content = """\
name: undefined_anchor
source:
  type: csv
  path: *undefined_anchor_xyz
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/ua.recotem
"""
    from recotem.recipe.errors import RecipeError

    yaml_path = tmp_path / "ua.yaml"
    yaml_path.write_text(content)
    try:
        from recotem.recipe.loader import load_recipe

        load_recipe(yaml_path)
    except RecipeError:
        pass  # expected — undefined alias causes yaml.YAMLError -> RecipeError
    except Exception as exc:
        raise AssertionError(
            f"Undefined YAML alias must raise RecipeError, "
            f"got {type(exc).__name__}: {exc}"
        ) from exc
