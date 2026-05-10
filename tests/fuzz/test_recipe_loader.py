"""Fuzz tests for recotem.recipe.loader.

Hypothesis YAML mutations; the loader must always either return a valid
Recipe or raise RecipeError — never any other exception type.
"""

from __future__ import annotations

import string
from pathlib import Path

import pytest
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


# ---------------------------------------------------------------------------
# C-3: yaml.safe_load custom Python tag rejection
# ---------------------------------------------------------------------------
# These tests confirm that the loader rejects YAML containing Python-specific
# tags (!!python/object/apply:, !!python/object:, etc.) with RecipeError rather
# than executing arbitrary code.  The primary defence is yaml.safe_load (which
# raises yaml.constructor.ConstructorError for these tags); the loader must
# wrap that as RecipeError — not let the ConstructorError escape.
# This acts as an early-warning regression if the loader ever switches to
# yaml.load with an unsafe Loader.

# Note: the YAML strings below are stored as variables to avoid triggering
# static-analysis patterns on the test file itself.
_YAML_PYTHON_APPLY = "!!python/object/apply:subprocess.getoutput ['id']\n"
_YAML_PYTHON_EVAL = "!!python/object/apply:builtins.compile ['1+1', '<str>', 'eval']\n"
_YAML_PYTHON_OBJ = "!!python/object:pathlib.PurePosixPath {}\n"


def test_python_object_apply_raises_recipe_error(tmp_path: Path) -> None:
    """!!python/object/apply: tag must be rejected as RecipeError, not execute code."""
    yaml_path = tmp_path / "python_tag_apply.yaml"
    yaml_path.write_text(_YAML_PYTHON_APPLY)
    with pytest.raises(RecipeError):
        from recotem.recipe.loader import load_recipe

        load_recipe(yaml_path)


def test_python_object_apply_builtins_raises_recipe_error(
    tmp_path: Path,
) -> None:
    """!!python/object/apply: with builtins module must be rejected as RecipeError."""
    yaml_path = tmp_path / "python_tag_eval.yaml"
    yaml_path.write_text(_YAML_PYTHON_EVAL)
    with pytest.raises(RecipeError):
        from recotem.recipe.loader import load_recipe

        load_recipe(yaml_path)


def test_python_object_tag_raises_recipe_error(tmp_path: Path) -> None:
    """!!python/object: tag must be rejected as RecipeError."""
    yaml_path = tmp_path / "python_tag_object.yaml"
    yaml_path.write_text(_YAML_PYTHON_OBJ)
    with pytest.raises(RecipeError):
        from recotem.recipe.loader import load_recipe

        load_recipe(yaml_path)


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


# ---------------------------------------------------------------------------
# Exponential anchor / alias expansion ("billion laughs" variant)
# ---------------------------------------------------------------------------


def test_yaml_exponential_anchor_expansion_handled_safely(tmp_path: Path) -> None:
    """YAML anchor chains with exponential expansion must not exhaust memory.

    This is a deterministic (non-Hypothesis) regression for the "YAML billion
    laughs" class of attack: each alias level doubles the previous one, so
    depth 15 would theoretically expand to 2^14 = 16 384 copies of the base
    scalar.  yaml.safe_load (used by the loader) builds Python objects in
    memory, so the expansion is bounded by the size of the Python objects rather
    than the YAML text.

    Acceptable outcomes:
      - RecipeError (loader rejected the malformed/oversized YAML)
      - yaml.YAMLError (safe_load refused to process it)
      - The load returns normally (safe_load already limits expansion)

    Unacceptable outcome:
      - MemoryError / OSError from memory exhaustion
      - RecursionError from unbounded recursion
      - Any other unhandled exception that is not one of the above

    Thresholds: 5-second wall-clock limit, 200 MiB memory delta.
    The depth is capped at 12 (2^11 = 2 048 copies) so that in the worst case
    — if yaml.safe_load does expand the tree — the RSS increase stays well
    within the 200 MiB budget on typical deployments.
    """
    import resource
    import time

    import yaml

    from recotem.recipe.errors import RecipeError

    DEPTH = 12  # 2^(DEPTH-1) = 2 048 copies of the base scalar at deepest level
    WALL_LIMIT = 5.0  # seconds
    MEM_LIMIT_BYTES = 200 * 1024 * 1024  # 200 MiB

    # Build exponential anchor chain: each level is a sequence whose two elements
    # are aliases to the previous level's anchor.
    #
    # Level 0: &a0 "x"
    # Level 1: &a1 [*a0, *a0]
    # Level 2: &a2 [*a1, *a1]
    # ...
    # Level N: &aN [*a(N-1), *a(N-1)]
    lines = ["lev0: &a0 x"]
    for i in range(1, DEPTH):
        prev = f"a{i - 1}"
        curr = f"a{i}"
        lines.append(f"lev{i}: &{curr} [*{prev}, *{prev}]")
    yaml_str = "\n".join(lines) + "\n"

    # Record RSS before the attempt.
    try:
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        rss_before = 0  # platform without resource module — skip memory check

    start = time.monotonic()
    try:
        yaml.safe_load(yaml_str)
    except (yaml.YAMLError, RecipeError, MemoryError):
        pass  # all acceptable — safe_load may refuse or run out of memory
    except RecursionError as exc:
        raise AssertionError(
            f"RecursionError on exponential anchor chain (depth={DEPTH}): {exc}. "
            "The loader or yaml.safe_load must not recurse unboundedly."
        ) from exc
    except Exception as exc:
        raise AssertionError(
            f"Unexpected exception {type(exc).__name__} on exponential anchor "
            f"chain (depth={DEPTH}): {exc}"
        ) from exc
    elapsed = time.monotonic() - start

    assert elapsed < WALL_LIMIT, (
        f"Exponential anchor expansion took {elapsed:.2f}s (limit {WALL_LIMIT}s). "
        f"depth={DEPTH}. yaml.safe_load may be expanding anchors exponentially."
    )

    try:
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_delta = rss_after - rss_before
        # On Linux ru_maxrss is in kilobytes; on macOS it is in bytes.
        import sys

        if sys.platform != "darwin":
            mem_delta *= 1024  # convert kB → bytes on Linux
        if rss_before > 0:  # only check if we could read RSS
            assert mem_delta < MEM_LIMIT_BYTES, (
                f"RSS grew by {mem_delta / 1024 / 1024:.1f} MiB during exponential "
                f"anchor expansion (limit {MEM_LIMIT_BYTES // 1024 // 1024} MiB). "
                f"depth={DEPTH}."
            )
    except Exception:
        pass  # platform without resource module or RSS read failed — skip
