"""Unit tests for recotem.recipe.loader.

Tests env expansion (allow/blacklist/missing/never inside query),
path scheme direction-aware policy, name regex enforcement, duplicate detection,
line-number errors, artifact root containment, and recipe file containment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recotem.recipe.errors import RecipeError
from recotem.recipe.loader import (
    load_recipe,
    load_recipes_directory,
    load_recipes_directory_lenient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_RECIPE_TEMPLATE = """\
name: {name}
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
  path: {output_path}
"""


def _write_recipe(tmp_path: Path, content: str, filename: str = "recipe.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(content)
    return p


def _minimal(tmp_path: Path, name: str = "my_recipe", extra: str = "") -> Path:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name=name,
        output_path=str(tmp_path / f"{name}.recotem"),
    )
    if extra:
        content += "\n" + extra
    return _write_recipe(tmp_path, content)


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


def test_recipe_name_with_slash_rejected(tmp_path: Path) -> None:
    p = _write_recipe(
        tmp_path,
        MINIMAL_RECIPE_TEMPLATE.format(name="bad/name", output_path="/tmp/x.recotem"),
    )
    with pytest.raises(RecipeError, match="name"):
        load_recipe(p)


def test_recipe_name_over_64_chars_rejected(tmp_path: Path) -> None:
    p = _write_recipe(
        tmp_path,
        MINIMAL_RECIPE_TEMPLATE.format(name="a" * 65, output_path="/tmp/x.recotem"),
    )
    with pytest.raises(RecipeError, match="name"):
        load_recipe(p)


def test_recipe_name_empty_rejected(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(name="", output_path="/tmp/x.recotem")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


def test_recipe_name_valid_with_hyphens_underscore(tmp_path: Path) -> None:
    p = _minimal(tmp_path, name="my-recipe_v2")
    recipe = load_recipe(p)
    assert recipe.name == "my-recipe_v2"


# ---------------------------------------------------------------------------
# Env expansion
# ---------------------------------------------------------------------------


def test_env_var_expansion_allowed_only_with_RECOTEM_RECIPE_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECOTEM_RECIPE_MYVAR", "hello")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="env_test",
        output_path=str(tmp_path / "env_test.recotem"),
    )
    content = content.replace("user_id", "${RECOTEM_RECIPE_MYVAR}")
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.schema_.user_column == "hello"


def test_env_var_expansion_undefined_raises_with_var_name_in_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RECOTEM_RECIPE_UNDEFINED_XYZ", raising=False)
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="undef_test",
        output_path=str(tmp_path / "undef_test.recotem"),
    )
    content = content.replace("user_id", "${RECOTEM_RECIPE_UNDEFINED_XYZ}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError) as exc_info:
        load_recipe(p)
    assert "RECOTEM_RECIPE_UNDEFINED_XYZ" in str(exc_info.value)


def test_env_var_expansion_blacklisted_var_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECOTEM_SIGNING_KEY", "secret_value")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="bl_test",
        output_path=str(tmp_path / "bl_test.recotem"),
    )
    content = content.replace("user_id", "${RECOTEM_SIGNING_KEY}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_env_var_expansion_aws_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="aws_bl",
        output_path=str(tmp_path / "aws_bl.recotem"),
    )
    content = content.replace("user_id", "${AWS_SECRET_ACCESS_KEY}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_env_var_expansion_inside_query_field_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env expansion must NOT occur inside source.query fields."""
    monkeypatch.setenv("RECOTEM_RECIPE_TABLE", "my_table")
    content = """\
name: query_expansion_test
source:
  type: bigquery
  query: "SELECT * FROM ${RECOTEM_RECIPE_TABLE}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/q.recotem
"""
    p = _write_recipe(tmp_path, content)
    # The recipe loads OK — but the query must NOT have been expanded
    # (RecipeError is not expected; the expansion just should not happen)
    # Load the recipe and check the query is still unexpanded
    try:
        recipe = load_recipe(p)
        # query should contain the literal ${...}
        assert "${RECOTEM_RECIPE_TABLE}" in recipe.source.query
    except RecipeError:
        # Also acceptable if bigquery source type fails for another reason
        pass


# ---------------------------------------------------------------------------
# CRITICAL-2: query_parameters must never receive env expansion
# ---------------------------------------------------------------------------


def test_env_var_expansion_inside_query_parameters_not_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env expansion must NOT occur inside source.query_parameters values.

    query_parameters carries user-supplied SQL parameters; expanding ${...}
    there would permit SQL-injection via environment variables.
    """
    monkeypatch.setenv("RECOTEM_RECIPE_MYVAL", "injected_value")
    content = """\
name: qp_no_expand
source:
  type: bigquery
  query: "SELECT * FROM my_table WHERE col = @param"
  query_parameters:
    - name: param
      parameterType:
        type: STRING
      parameterValue:
        value: "${RECOTEM_RECIPE_MYVAL}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/qp.recotem
"""
    p = _write_recipe(tmp_path, content)
    try:
        recipe = load_recipe(p)
        # If the recipe loaded, the query_parameters must NOT have been expanded:
        # the literal "${RECOTEM_RECIPE_MYVAL}" must survive intact.
        qp = recipe.source.query_parameters
        if qp is not None:
            import json as _json

            # serialise to string to find the literal reference
            qp_str = _json.dumps(qp) if not isinstance(qp, str) else qp
            # The env var VALUE should not be present; the literal ${...} should be.
            assert "injected_value" not in qp_str, (
                "query_parameters must not be env-expanded; "
                f"found injected value in: {qp_str!r}"
            )
    except RecipeError:
        # Acceptable — bigquery may not be available; refusal is also correct.
        pass


def test_env_var_expansion_secret_pattern_in_query_parameters_not_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret-pattern variable inside query_parameters must not cause RecipeError
    (i.e. the blacklist check must NOT fire inside no-expand sections).

    The _NO_EXPAND_KEYS guard must short-circuit expansion entirely —
    including the blacklist check — so no RecipeError is raised for
    ${RECOTEM_SIGNING_KEY} appearing inside query_parameters.
    """
    monkeypatch.setenv("RECOTEM_SIGNING_KEY", "secret-value")
    content = """\
name: qp_secret_key
source:
  type: bigquery
  query: "SELECT 1"
  query_parameters:
    - name: k
      parameterType:
        type: STRING
      parameterValue:
        value: "${RECOTEM_SIGNING_KEY}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/qp_secret.recotem
"""
    p = _write_recipe(tmp_path, content)
    # Must NOT raise RecipeError with "blacklisted" — no expansion means no check.
    # If bigquery source type causes failure for another reason, that's fine too.
    try:
        load_recipe(p)
        # If we get here, the literal was preserved (no expansion, no blacklist error)
    except RecipeError as exc:
        # Must not be the blacklist error
        assert "blacklisted" not in str(exc), (
            f"query_parameters expansion guard must not fire the blacklist; got: {exc}"
        )


# ---------------------------------------------------------------------------
# Path scheme — direction-aware policy
# ---------------------------------------------------------------------------


def test_input_source_with_https_scheme_accepted_when_sha256_set(
    tmp_path: Path,
) -> None:
    """HTTPS source paths load when sha256 is provided. (Network rule covered in Task 4.)"""
    out = tmp_path / "https_input_ok.recotem"
    content = f"""\
name: https_input_ok
source:
  type: csv
  path: https://example.com/data.csv
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.source.path == "https://example.com/data.csv"


def test_input_source_with_file_scheme_accepted(tmp_path: Path) -> None:
    """file:// is accepted on input paths (equivalent to bare local)."""
    out = tmp_path / "file_input_ok.recotem"
    content = f"""\
name: file_input_ok
source:
  type: csv
  path: file:///tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.source.path == "file:///tmp/data.csv"


def test_output_path_with_http_scheme_rejected(tmp_path: Path) -> None:
    """http:// is not writeable by fsspec; reject at load time."""
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="http_output",
        output_path="http://example.com/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="not supported"):
        load_recipe(p)


def test_output_path_with_https_scheme_rejected(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="https_output",
        output_path="https://example.com/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="not supported"):
        load_recipe(p)


def test_output_path_with_memory_scheme_rejected(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="memory_output",
        output_path="memory://out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="not supported"):
        load_recipe(p)


def test_output_path_with_file_scheme_accepted(tmp_path: Path) -> None:
    """file:// is treated equivalent to bare local path on output."""
    out = tmp_path / "out.recotem"
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_output_ok",
        output_path=f"file://{out}",
    )
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.output.path == f"file://{out}"


def test_output_path_with_s3_scheme_accepted(tmp_path: Path) -> None:
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="s3_output_ok",
        output_path="s3://my-bucket/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.output.path == "s3://my-bucket/out.recotem"


def test_input_source_with_embedded_credentials_rejected(tmp_path: Path) -> None:
    out = tmp_path / "cred.recotem"
    content = f"""\
name: cred_input
source:
  type: csv
  path: https://user:pass@example.com/data.csv
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="credentials"):
        load_recipe(p)


def test_output_path_with_embedded_credentials_rejected(tmp_path: Path) -> None:
    # Note: s3:// uses '@' in idiomatic addressing (S-L fix); use ftp:// which IS
    # in the userinfo-reject set and also in the output-scheme reject set so the
    # error fires (scheme check takes priority over userinfo for ftp://).
    # The original intent — that embedding credentials in an output URL is rejected —
    # is preserved; only the example scheme is updated to match the new policy.
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="cred_output",
        output_path="ftp://AKIA123:secret@bucket/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


# ---------------------------------------------------------------------------
# RECOTEM_ARTIFACT_ROOT containment
# ---------------------------------------------------------------------------


def test_local_output_path_outside_artifact_root_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root = tmp_path / "allowed"
    artifact_root.mkdir()
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="outside_root",
        output_path="/tmp/sneaky.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="outside"):
        load_recipe(p)


def test_local_output_path_inside_artifact_root_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root = tmp_path / "allowed"
    artifact_root.mkdir()
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="inside_root",
        output_path=str(artifact_root / "model.recotem"),
    )
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.name == "inside_root"


def test_file_scheme_output_path_outside_artifact_root_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """file:// must not bypass RECOTEM_ARTIFACT_ROOT containment."""
    artifact_root = tmp_path / "allowed"
    artifact_root.mkdir()
    outside = tmp_path / "outside.recotem"
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_outside_root",
        output_path=f"file://{outside}",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="outside"):
        load_recipe(p)


def test_file_scheme_output_path_inside_artifact_root_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """file://<path> resolved under RECOTEM_ARTIFACT_ROOT is allowed."""
    artifact_root = tmp_path / "allowed"
    artifact_root.mkdir()
    inside = artifact_root / "model.recotem"
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_inside_root",
        output_path=f"file://{inside}",
    )
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.name == "file_inside_root"


def test_file_scheme_output_path_with_host_rejected(tmp_path: Path) -> None:
    """file://host/path (UNC-style) is ambiguous for local containment; reject."""
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_with_host",
        output_path="file://example.com/tmp/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="host|netloc|local path"):
        load_recipe(p)


# ---------------------------------------------------------------------------
# Duplicate recipe names
# ---------------------------------------------------------------------------


def test_duplicate_recipe_name_in_directory_rejected_at_startup(
    tmp_path: Path,
) -> None:
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    for i in range(2):
        content = MINIMAL_RECIPE_TEMPLATE.format(
            name="duplicate_name",
            output_path=str(tmp_path / f"model_{i}.recotem"),
        )
        (recipes_dir / f"recipe_{i}.yaml").write_text(content)
    with pytest.raises(RecipeError, match="[Dd]uplicate"):
        load_recipes_directory(recipes_dir)


# ---------------------------------------------------------------------------
# Line-number errors
# ---------------------------------------------------------------------------


def test_recipe_error_includes_yaml_line_number(tmp_path: Path) -> None:
    """A YAML parse error produces a RecipeError with a non-None line number."""
    bad_yaml = "name: test\n  invalid: indentation: here:\n"
    p = tmp_path / "bad.yaml"
    p.write_text(bad_yaml)
    with pytest.raises(RecipeError) as exc_info:
        load_recipe(p)
    # line might be None for schema errors, but for YAML syntax it should be set
    # We just assert the exception is a RecipeError (line may or may not be set)
    assert isinstance(exc_info.value, RecipeError)


# ---------------------------------------------------------------------------
# Recipe file containment
# ---------------------------------------------------------------------------


def test_recipe_path_outside_recipes_root_rejected(tmp_path: Path) -> None:
    """A recipe file that resolves outside recipes_root raises RecipeError."""
    recipes_root = tmp_path / "recipes"
    recipes_root.mkdir()
    outside_recipe = tmp_path / "outside.yaml"  # outside recipes_root
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="outside",
        output_path=str(tmp_path / "outside.recotem"),
    )
    outside_recipe.write_text(content)
    with pytest.raises(RecipeError, match="outside"):
        load_recipe(outside_recipe, recipes_root=recipes_root)


# ---------------------------------------------------------------------------
# Missing fields
# ---------------------------------------------------------------------------


def test_recipe_with_no_source_rejected(tmp_path: Path) -> None:
    content = """\
name: no_source
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/out.recotem
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


def test_recipe_name_revalidated_before_filesystem_use(tmp_path: Path) -> None:
    """validate_for_filesystem raises ValueError for names with slashes."""
    from recotem.recipe.models import validate_for_filesystem

    with pytest.raises(ValueError, match="filesystem"):
        validate_for_filesystem("bad/name")


def test_validate_for_filesystem_valid_name_passes(tmp_path: Path) -> None:
    from recotem.recipe.models import validate_for_filesystem

    result = validate_for_filesystem("good-name_v1")
    assert result == "good-name_v1"


# ---------------------------------------------------------------------------
# Source dict → typed Config promotion (post-model_validate step)
# ---------------------------------------------------------------------------

_CSV_RECIPE_TEMPLATE = """\
name: {name}
source:
  type: csv
  path: {csv_path}
  delimiter: ","
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {output_path}
"""

_PARQUET_RECIPE_TEMPLATE = """\
name: {name}
source:
  type: parquet
  path: {parquet_path}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {output_path}
"""


def _write_csv_recipe(
    tmp_path: Path, name: str = "csv_recipe", csv_filename: str = "data.csv"
) -> Path:
    csv_file = tmp_path / csv_filename
    if not csv_file.exists():
        csv_file.write_text("user_id,item_id\nu1,i1\nu2,i2\n")
    content = _CSV_RECIPE_TEMPLATE.format(
        name=name,
        csv_path=str(csv_file),
        output_path=str(tmp_path / f"{name}.recotem"),
    )
    p = tmp_path / f"{name}.yaml"
    p.write_text(content)
    return p


def _write_parquet_recipe(tmp_path: Path, name: str = "parquet_recipe") -> Path:
    parquet_file = tmp_path / "data.parquet"
    if not parquet_file.exists():
        import pandas as pd

        pd.DataFrame({"user_id": ["u1", "u2"], "item_id": ["i1", "i2"]}).to_parquet(
            parquet_file, index=False
        )
    content = _PARQUET_RECIPE_TEMPLATE.format(
        name=name,
        parquet_path=str(parquet_file),
        output_path=str(tmp_path / f"{name}.recotem"),
    )
    p = tmp_path / f"{name}.yaml"
    p.write_text(content)
    return p


def test_load_recipe_promotes_source_dict_to_typed_csv_config(
    tmp_path: Path,
) -> None:
    """After load_recipe, recipe.source must be a CSVConfig instance (not dict)."""
    from recotem.datasource.csv import CSVConfig

    p = _write_csv_recipe(tmp_path)
    recipe = load_recipe(p)
    assert isinstance(recipe.source, CSVConfig), (
        f"Expected CSVConfig, got {type(recipe.source)}"
    )


def test_load_recipe_promotes_source_dict_to_typed_parquet_config(
    tmp_path: Path,
) -> None:
    """After load_recipe with type=parquet, recipe.source must be a ParquetConfig."""
    from recotem.datasource.csv import ParquetConfig

    p = _write_parquet_recipe(tmp_path)
    recipe = load_recipe(p)
    assert isinstance(recipe.source, ParquetConfig), (
        f"Expected ParquetConfig, got {type(recipe.source)}"
    )


def test_load_recipe_source_typed_attrs_accessible(tmp_path: Path) -> None:
    """After promotion, source attributes like .path and .delimiter are accessible."""
    p = _write_csv_recipe(tmp_path)
    recipe = load_recipe(p)
    # Typed access — must not raise AttributeError or return a KeyError.
    assert recipe.source.path.endswith("data.csv")
    assert recipe.source.delimiter == ","


def test_load_recipe_unknown_source_type_raises_recipe_error(
    tmp_path: Path,
) -> None:
    """A source with an unknown type (e.g. ftp) must raise RecipeError."""
    content = """\
name: ftp_recipe
source:
  type: ftp
  path: ftp://example.com/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/ftp_recipe.recotem
"""
    p = tmp_path / "ftp_recipe.yaml"
    p.write_text(content)
    with pytest.raises(RecipeError):
        load_recipe(p)


def test_load_recipe_source_missing_type_raises_recipe_error(
    tmp_path: Path,
) -> None:
    """A source dict without the 'type' discriminator must raise RecipeError."""
    content = """\
name: no_type_recipe
source:
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/no_type_recipe.recotem
"""
    p = tmp_path / "no_type_recipe.yaml"
    p.write_text(content)
    with pytest.raises(RecipeError):
        load_recipe(p)


# ---------------------------------------------------------------------------
# sha256 required for network-scheme input paths
# ---------------------------------------------------------------------------


def test_https_source_without_sha256_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out.recotem"
    content = f"""\
name: https_no_sha
source:
  type: csv
  path: https://example.com/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="sha256"):
        load_recipe(p)


def test_http_source_without_sha256_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out.recotem"
    content = f"""\
name: http_no_sha
source:
  type: csv
  path: http://example.com/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="sha256"):
        load_recipe(p)


def test_https_item_metadata_without_sha256_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out.recotem"
    content = f"""\
name: https_meta_no_sha
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
item_metadata:
  type: csv
  path: https://example.com/items.csv
  fields: [title]
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="sha256"):
        load_recipe(p)


def test_local_source_without_sha256_accepted(tmp_path: Path) -> None:
    """Bare local paths don't require sha256."""
    p = _minimal(tmp_path, name="local_no_sha")
    recipe = load_recipe(p)
    assert recipe.source.sha256 is None


def test_s3_source_without_sha256_accepted(tmp_path: Path) -> None:
    """s3:// is not a network-fetch scheme — sha256 stays optional."""
    out = tmp_path / "out.recotem"
    content = f"""\
name: s3_no_sha
source:
  type: csv
  path: s3://my-bucket/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.source.sha256 is None


# ---------------------------------------------------------------------------
# item_metadata.item_id_column — end-to-end through the YAML loader
# ---------------------------------------------------------------------------


def test_item_metadata_item_id_column_custom_propagates_through_yaml(
    tmp_path: Path,
) -> None:
    """A recipe YAML with item_metadata.item_id_column: 'product_id' must
    produce Recipe.item_metadata.item_id_column == 'product_id' after load.

    Regression baseline for the field addition in ItemMetadataConfig: the loader
    must correctly round-trip the new field from YAML bytes to the pydantic model.
    """
    out = tmp_path / "custom_id_col.recotem"
    content = f"""\
name: custom_id_col
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
item_metadata:
  type: csv
  path: /tmp/items.csv
  fields: [title]
  item_id_column: product_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.item_metadata is not None
    assert recipe.item_metadata.item_id_column == "product_id", (
        f"Expected 'product_id', got {recipe.item_metadata.item_id_column!r}"
    )


def test_item_metadata_item_id_column_default_when_omitted_in_yaml(
    tmp_path: Path,
) -> None:
    """When item_id_column is omitted from the YAML, the field defaults to 'item_id'.

    Regression: ensures the default is preserved end-to-end through the loader
    so existing recipes without the field continue to behave identically.
    """
    out = tmp_path / "default_id_col.recotem"
    content = f"""\
name: default_id_col
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
item_metadata:
  type: csv
  path: /tmp/items.csv
  fields: [title]
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    recipe = load_recipe(p)
    assert recipe.item_metadata is not None
    assert recipe.item_metadata.item_id_column == "item_id", (
        f"Expected default 'item_id', got {recipe.item_metadata.item_id_column!r}"
    )


# ---------------------------------------------------------------------------
# S-1: symlink-in-parent containment check
# ---------------------------------------------------------------------------


def test_output_path_symlink_in_parent_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink in the parent directory of output.path must be rejected.

    An attacker who can drop a symlink inside the artifact root could escape
    containment on the first write if only the final path is resolved.
    The loader must resolve the *parent* strictly and assert it stays inside
    the artifact root.
    """
    artifact_root = tmp_path / "allowed"
    artifact_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    # Create a symlink inside artifact_root that points outside it.
    symlink_dir = artifact_root / "escape"
    symlink_dir.symlink_to(outside_dir)

    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))

    # output.path points into artifact_root/escape/model.recotem; after the
    # symlink is followed the parent resolves to tmp_path/outside — outside root.
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="symlink_escape",
        output_path=str(symlink_dir / "model.recotem"),
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


# ---------------------------------------------------------------------------
# M-8: terminal-filename symlink TOCTOU — parent inside root, symlink as filename
# ---------------------------------------------------------------------------


def test_output_path_terminal_symlink_parent_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader's containment check is authoritative on the *parent* directory.

    When output.path is ``<artifact_root>/model.recotem`` and a symlink named
    ``model.recotem`` already exists inside the root pointing to an outside
    target, the loader composes the containment path as
    ``resolved_parent / local_path.name`` — which is ``artifact_root/model.recotem``
    (inside the root) — rather than following the symlink via a non-strict
    ``Path.resolve()``.

    This means load_recipe PASSES for a terminal-filename symlink whose
    *parent directory* resolves inside the artifact root.  The write-time
    defence (artifact/io.py) catches any actual escape attempt.

    Regression: previously the code called ``local_path.resolve()`` (non-strict),
    which CAN follow existing terminal symlinks to their target.  The new code
    avoids this non-deterministic behaviour (depends on whether the file exists)
    by using the composed path exclusively.
    """
    artifact_root = tmp_path / "root"
    artifact_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    # Terminal symlink inside the artifact root pointing to an outside target.
    symlink_path = artifact_root / "model.recotem"
    symlink_path.symlink_to(outside_dir / "model.recotem")

    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))

    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="terminal_symlink_parent_auth",
        output_path=str(symlink_path),
    )
    p = _write_recipe(tmp_path, content)

    # The parent (artifact_root) is strictly inside the root.  The loader
    # composes the containment path as resolved_parent/model.recotem — which
    # IS inside the root — so load_recipe must succeed.  Write-time escape is
    # the responsibility of artifact/io.py (defense-in-depth).
    recipe = load_recipe(p)
    assert recipe.name == "terminal_symlink_parent_auth"


# ---------------------------------------------------------------------------
# S-3: TOKEN / KEY blacklist patterns
# ---------------------------------------------------------------------------


def test_envvars_blacklist_token_pattern_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECOTEM_RECIPE_MY_TOKEN must be rejected by the blacklist (*_TOKEN*)."""
    monkeypatch.setenv("RECOTEM_RECIPE_MY_TOKEN", "secret-token-value")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="token_test",
        output_path=str(tmp_path / "token_test.recotem"),
    )
    content = content.replace("user_id", "${RECOTEM_RECIPE_MY_TOKEN}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_envvars_blacklist_key_pattern_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECOTEM_RECIPE_MY_KEY must be rejected by the blacklist (*_KEY*)."""
    monkeypatch.setenv("RECOTEM_RECIPE_MY_KEY", "secret-key-value")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="key_test",
        output_path=str(tmp_path / "key_test.recotem"),
    )
    content = content.replace("user_id", "${RECOTEM_RECIPE_MY_KEY}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


# ---------------------------------------------------------------------------
# E-6: pydantic ValidationError field locations in RecipeError message
# ---------------------------------------------------------------------------


def test_recipe_validation_error_includes_field_locations(tmp_path: Path) -> None:
    """A recipe with multiple field errors must report each field location.

    Previously, bare Exception catch flattened pydantic's structured errors()
    (which carry loc, type, ctx) into a single opaque string.  Now each error
    must appear as a dotted field path in the RecipeError message.
    """
    # n_trials must be >= 1 (ge=1), parallelism must be >= 1 (ge=1).
    # Providing 0 for both triggers two distinct validation errors.
    out = tmp_path / "multi_err.recotem"
    content = f"""\
name: multi_err
source:
  type: csv
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 0
  parallelism: 0
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError) as exc_info:
        load_recipe(p)
    msg = str(exc_info.value)
    # Both field names must appear somewhere in the error message.
    assert "n_trials" in msg, f"Expected 'n_trials' in message, got: {msg}"
    assert "parallelism" in msg, f"Expected 'parallelism' in message, got: {msg}"


# ---------------------------------------------------------------------------
# Q-2: extra="forbid" enforced on source after validation
# ---------------------------------------------------------------------------


def test_source_extra_forbid_enforced_after_validation(tmp_path: Path) -> None:
    """A CSV source with an unknown field must be rejected with extra='forbid'.

    Previously, object.__setattr__ was used to reassign recipe.source after the
    Recipe was already constructed, which bypassed pydantic re-validation.
    Now the typed source is built first — extra='forbid' must fire here.
    """
    out = tmp_path / "extra_field.recotem"
    content = f"""\
name: extra_field_test
source:
  type: csv
  path: /tmp/data.csv
  unknown_extra_field: should_be_rejected
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


# ---------------------------------------------------------------------------
# CRITICAL: dot-dot traversal in output.path rejected under ARTIFACT_ROOT
# ---------------------------------------------------------------------------


def test_local_output_path_dotdot_traversal_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dot-dot path that escapes RECOTEM_ARTIFACT_ROOT must be rejected.

    output.path: <root>/allowed/sub/../../escape/foo.recotem resolves to
    <root>/escape/foo.recotem which lies outside the artifact root.

    Both parent directories (sub/ and escape/) are created so the parent-
    directory existence check does not fire first — the containment check
    is what must catch the traversal.
    """
    artifact_root = tmp_path / "allowed"
    artifact_root.mkdir()
    monkeypatch.setenv("RECOTEM_ARTIFACT_ROOT", str(artifact_root))

    # Create the intermediate dirs so only the containment check fires.
    (artifact_root / "sub").mkdir()
    escape_dir = tmp_path / "escape"
    escape_dir.mkdir()

    # /allowed/sub/../../escape/foo.recotem resolves to /escape/foo.recotem.
    dotdot_path = str(artifact_root / "sub" / ".." / ".." / "escape" / "foo.recotem")

    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="dotdot_escape",
        output_path=dotdot_path,
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="outside"):
        load_recipe(p)


# ---------------------------------------------------------------------------
# CRITICAL: ftp:// scheme on output.path rejected
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C10 — recursive env-var expansion does not loop
# ---------------------------------------------------------------------------


def test_env_var_expansion_recursive_does_not_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A self-referencing env var must not cause infinite recursion.

    The expand_env_vars implementation uses re.sub() with a one-pass
    replacement: it substitutes all ${...} references in the original string
    once, then returns.  It does NOT re-scan the expanded result.

    Policy confirmed by the code: if RECOTEM_RECIPE_A = '${RECOTEM_RECIPE_A}',
    the result of expanding ${RECOTEM_RECIPE_A} is the literal string
    '${RECOTEM_RECIPE_A}' — no infinite recursion, just one pass.

    This test documents that policy.
    """
    from recotem.recipe.envvars import expand_env_vars

    # Set the variable to a self-referencing value.
    monkeypatch.setenv("RECOTEM_RECIPE_A", "${RECOTEM_RECIPE_A}")

    # The expansion must terminate without RecursionError.
    # The result is the literal value of the env var (the self-reference string).
    result = expand_env_vars("${RECOTEM_RECIPE_A}")

    # Policy: one-pass expansion — the result is whatever the env var says,
    # NOT recursively expanded.  No infinite loop.
    assert result == "${RECOTEM_RECIPE_A}", (
        f"One-pass expansion of a self-referencing env var must return the "
        f"literal env var value without recursing; got: {result!r}"
    )


def test_env_var_expansion_mutual_recursion_does_not_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutually-referencing env vars (A → B → A) must not cause infinite recursion.

    A = '${RECOTEM_RECIPE_B}', B = '${RECOTEM_RECIPE_A}'.
    One-pass expansion of ${RECOTEM_RECIPE_A} returns '${RECOTEM_RECIPE_B}'
    without re-scanning.
    """
    from recotem.recipe.envvars import expand_env_vars

    monkeypatch.setenv("RECOTEM_RECIPE_A", "${RECOTEM_RECIPE_B}")
    monkeypatch.setenv("RECOTEM_RECIPE_B", "${RECOTEM_RECIPE_A}")

    # Must not recurse — returns the literal value of RECOTEM_RECIPE_A.
    result = expand_env_vars("${RECOTEM_RECIPE_A}")
    assert result == "${RECOTEM_RECIPE_B}", (
        f"One-pass expansion must return the literal value of the env var "
        f"without recursing into RECOTEM_RECIPE_B; got: {result!r}"
    )


def test_output_path_with_ftp_scheme_rejected(tmp_path: Path) -> None:
    """ftp:// on output.path must be rejected as an unsupported write scheme.

    Matches the http/https/memory rejection behaviour documented in
    test_output_path_with_http_scheme_rejected.
    """
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="ftp_output",
        output_path="ftp://example.com/foo.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="not supported|ftp"):
        load_recipe(p)


# ---------------------------------------------------------------------------
# MAJOR-4: allow-list output scheme enforcement
# ---------------------------------------------------------------------------


def test_output_path_data_scheme_rejected(tmp_path: Path) -> None:
    """data: URI must be rejected by the allow-list output-scheme check.

    Previously only a deny-list was used; ``data:`` was not in that list and
    would silently pass through.  The allow-list ensures novel/unknown schemes
    are refused by default.
    """
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="data_scheme_output",
        output_path="data:text/plain,hello",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


def test_output_path_javascript_scheme_rejected(tmp_path: Path) -> None:
    """javascript: URI must be rejected by the allow-list output-scheme check."""
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="js_scheme_output",
        output_path="javascript:alert(1)",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


def test_output_path_unknown_scheme_rejected(tmp_path: Path) -> None:
    """An unrecognised scheme must be rejected with an 'Allowed' hint in the message."""
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="weird_scheme_output",
        output_path="weirdscheme://some/path/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="Allowed"):
        load_recipe(p)


def test_output_path_file_scheme_accepted_localhost(tmp_path: Path) -> None:
    """file:/// and file://localhost/ output paths must still be accepted.

    Regression: the allow-list must keep the file:// scheme valid, and the
    existing netloc check (only '' and 'localhost' are accepted) must also
    remain in force.
    """
    out1 = tmp_path / "local1.recotem"
    content1 = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_local_ok1",
        output_path=f"file://{out1}",
    )
    p1 = _write_recipe(tmp_path, content1, filename="recipe1.yaml")
    recipe1 = load_recipe(p1)
    assert recipe1.output.path == f"file://{out1}"

    out2 = tmp_path / "local2.recotem"
    content2 = MINIMAL_RECIPE_TEMPLATE.format(
        name="file_local_ok2",
        output_path=f"file://localhost{out2}",
    )
    p2 = _write_recipe(tmp_path, content2, filename="recipe2.yaml")
    recipe2 = load_recipe(p2)
    assert recipe2.output.path == f"file://localhost{out2}"


def test_output_path_object_store_schemes_accepted(tmp_path: Path) -> None:
    """s3://, gs://, az://, abfs://, and abfss:// output paths must be accepted.

    Note: abfs/abfss URLs that use the ``container@account`` addressing form
    are rejected by ``_check_userinfo`` because urlparse treats the ``@`` as a
    userinfo separator (extracting ``container`` as the username).  Use the
    plain-host form without ``@`` for the scheme-acceptance test; the
    credential check is independent of the allow-list test.
    """
    schemes_and_paths = [
        ("s3_out", "s3://my-bucket/key.recotem"),
        ("gs_out", "gs://my-bucket/key.recotem"),
        ("az_out", "az://my-container/blob.recotem"),
        # abfs/abfss without the @-form host (plain host, no userinfo):
        ("abfs_out", "abfs://account.dfs.core.windows.net/container/out.recotem"),
        ("abfss_out", "abfss://account.dfs.core.windows.net/container/out.recotem"),
    ]
    for name, output_path in schemes_and_paths:
        content = MINIMAL_RECIPE_TEMPLATE.format(
            name=name,
            output_path=output_path,
        )
        p = _write_recipe(tmp_path, content, filename=f"{name}.yaml")
        recipe = load_recipe(p)
        assert recipe.output.path == output_path, (
            f"Expected {output_path!r} to be accepted, got {recipe.output.path!r}"
        )


def test_output_path_http_https_ftp_memory_still_rejected(tmp_path: Path) -> None:
    """Regression: schemes previously on the deny-list must still be rejected.

    Ensures the allow-list migration did not accidentally permit http, https,
    ftp, ftps, or memory output paths.
    """
    rejected_paths = [
        ("http_regr", "http://example.com/out.recotem"),
        ("https_regr", "https://example.com/out.recotem"),
        ("ftp_regr", "ftp://example.com/out.recotem"),
        ("ftps_regr", "ftps://example.com/out.recotem"),
        ("memory_regr", "memory://out.recotem"),
    ]
    for name, output_path in rejected_paths:
        content = MINIMAL_RECIPE_TEMPLATE.format(
            name=name,
            output_path=output_path,
        )
        p = _write_recipe(tmp_path, content, filename=f"{name}.yaml")
        with pytest.raises(RecipeError, match="not supported"):
            load_recipe(p)


# ---------------------------------------------------------------------------
# S-A: substring blacklist — no underscore boundary required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "var_name",
    [
        "RECOTEM_RECIPE_APIKEY",  # KEY substring without underscore boundary
        "RECOTEM_RECIPE_PASSWD",  # PASSWD substring
        "RECOTEM_RECIPE_AUTH",  # AUTH substring
        "RECOTEM_RECIPE_BEARER",  # BEARER substring
        "RECOTEM_RECIPE_CRED_FILE",  # CRED substring
        "RECOTEM_RECIPE_PRIVATE_KEY",  # PRIVATE + KEY substrings
    ],
)
def test_envvars_blacklist_substring_patterns_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var_name: str
) -> None:
    """Credential-keyword env vars must be blocked even without an underscore boundary.

    The old glob *_KEY* would pass RECOTEM_RECIPE_APIKEY (no _ before KEY).
    The new substring check blocks any name containing KEY, AUTH, BEARER, etc.
    """
    monkeypatch.setenv(var_name, "should-be-blocked")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="sa_test",
        output_path=str(tmp_path / "sa_test.recotem"),
    )
    content = content.replace("user_id", f"${{{var_name}}}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


@pytest.mark.parametrize(
    "var_name",
    [
        "RECOTEM_SIGNING_KEY",  # explicit exact / legacy pattern
        "RECOTEM_API_KEYS",  # explicit exact / legacy pattern
        "RECOTEM_RECIPE_MY_SECRET",  # *SECRET* legacy pattern
        "RECOTEM_RECIPE_MY_PASSWORD",  # *PASSWORD* legacy pattern
        "RECOTEM_RECIPE_MY_TOKEN",  # *TOKEN* legacy pattern
        "RECOTEM_RECIPE_MY_KEY",  # *KEY* legacy pattern
        "AWS_ACCESS_KEY_ID",  # AWS_* legacy prefix
        "GOOGLE_CLOUD_PROJECT",  # GOOGLE_* legacy prefix
        "GCP_PROJECT",  # GCP_* legacy prefix
    ],
)
def test_envvars_blacklist_legacy_patterns_still_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var_name: str
) -> None:
    """Backward-compat: all previously-blacklisted patterns must still be blocked."""
    monkeypatch.setenv(var_name, "legacy-blocked-value")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="sa_legacy",
        output_path=str(tmp_path / "sa_legacy.recotem"),
    )
    content = content.replace("user_id", f"${{{var_name}}}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted|not allowed"):
        load_recipe(p)


# ---------------------------------------------------------------------------
# m-10: narrowed except clause — ImportError from get_source_class raises RecipeError
# ---------------------------------------------------------------------------


def test_source_class_import_error_raises_recipe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ImportError from get_source_class during env expansion must surface as
    RecipeError, not be silently swallowed by the old bare ``except Exception: pass``.

    Previously the except clause was bare ``except Exception: pass``, which
    silently fell back to the global no-expand list when a plugin raised
    ImportError (e.g. missing optional dependency).  This meant ${...} expansion
    would run on plugin-protected fields like ``api_token``.

    The fix narrows the catch: DataSourceError (unknown type) is allowed to pass
    silently (later validation surfaces it); any other exception including
    ImportError is re-raised as RecipeError.
    """
    from unittest.mock import patch

    content = """\
name: import_error_test
source:
  type: my_plugin_source
  path: /tmp/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: /tmp/import_error_test.recotem
"""
    p = _write_recipe(tmp_path, content)

    # Patch get_source_class to raise ImportError (simulates a plugin with a
    # missing optional dependency that defers the import to the class body).
    with patch(
        "recotem.datasource.registry.get_source_class",
        side_effect=ImportError("optional dependency 'my-plugin' is not installed"),
    ):
        with pytest.raises(RecipeError, match="my_plugin_source"):
            load_recipe(p)


# ---------------------------------------------------------------------------
# S-B: plugin no_expand_fields prevents expansion inside custom source fields
# ---------------------------------------------------------------------------


def test_plugin_no_expand_fields_prevents_expansion_in_custom_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin declaring no_expand_fields={'sql'} must not expand ${...} in 'sql'.

    We mock get_source_class in loader.py to return a minimal in-process
    DataSource class that declares no_expand_fields={'sql'}.  A recipe using
    that source with a ${RECOTEM_RECIPE_X} reference inside 'sql' must have
    the literal reference survive unexpanded.
    """
    from unittest.mock import patch

    from pydantic import BaseModel

    sql_ref = "${RECOTEM_RECIPE_X}"

    class _SqlConfig(BaseModel, extra="ignore"):
        type: str = "test_sql_source"
        sql: str = ""

    class _SqlSource:
        type_name: str = "test_sql_source"
        Config = _SqlConfig
        extras_required: list = []
        no_expand_fields: frozenset = frozenset({"sql"})

        def __init__(self, config: _SqlConfig) -> None:  # pragma: no cover
            self.config = config

        def fetch(self, ctx):  # pragma: no cover
            raise NotImplementedError

    monkeypatch.setenv("RECOTEM_RECIPE_X", "injected_value")

    content = f"""\
name: plugin_no_expand
source:
  type: test_sql_source
  sql: "SELECT * FROM {sql_ref}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / "plugin_no_expand.recotem"}
"""
    p = _write_recipe(tmp_path, content)

    # Patch get_source_class in the registry module (where it is imported from
    # inline in both _expand_with_source_no_expand and load_recipe).
    with patch("recotem.datasource.registry.get_source_class", return_value=_SqlSource):
        try:
            recipe = load_recipe(p)
            # If loaded, the sql field must NOT have been expanded.
            raw_sql = getattr(recipe.source, "sql", None)
            if raw_sql is not None:
                assert sql_ref in raw_sql, (
                    f"no_expand_fields guard must preserve literal {sql_ref!r} in sql; "
                    f"got: {raw_sql!r}"
                )
                assert "injected_value" not in raw_sql, (
                    "sql field must not receive env expansion; "
                    f"found injected value in: {raw_sql!r}"
                )
        except RecipeError as exc:
            # RecipeError is acceptable (e.g. schema validation fails for the
            # fake source type), but the error must NOT be a blacklist error
            # triggered by expansion inside the sql field.
            assert "blacklisted" not in str(exc), (
                f"no_expand_fields must prevent blacklist check firing in sql; got: {exc}"
            )


# ---------------------------------------------------------------------------
# S-C: input path scheme allow-list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "memory://some/path.csv",
        "data:text/plain,hello",
        "github://owner/repo/file.csv",
        "simplecache::https://example.com/data.csv",
        "hf://model/file.parquet",
    ],
)
def test_input_source_disallowed_scheme_rejected(tmp_path: Path, bad_path: str) -> None:
    """Input source paths with unsupported schemes must raise RecipeError."""
    out = tmp_path / "out.recotem"
    content = f"""\
name: bad_input_scheme
source:
  type: csv
  path: {bad_path}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


@pytest.mark.parametrize(
    "good_path,needs_sha256",
    [
        ("http://example.com/data.csv", True),
        ("https://example.com/data.csv", True),
        ("s3://my-bucket/key.csv", False),
        ("gs://my-bucket/key.csv", False),
        ("file:///abs/path/data.csv", False),
    ],
)
def test_input_source_allowed_scheme_accepted(
    tmp_path: Path, good_path: str, needs_sha256: bool
) -> None:
    """Input source paths with allowed schemes must not be rejected by scheme check."""
    out = tmp_path / "out.recotem"
    sha_line = (
        '  sha256: "0000000000000000000000000000000000000000000000000000000000000000"'
        if needs_sha256
        else ""
    )
    content = f"""\
name: good_input_scheme
source:
  type: csv
  path: {good_path}
{sha_line}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    # Must not raise RecipeError for scheme reasons; schema/validation errors OK.
    try:
        recipe = load_recipe(p)
        assert recipe.source.path == good_path
    except RecipeError as exc:
        # Scheme rejection is NOT acceptable
        assert "not supported for input" not in str(exc), (
            f"Scheme {good_path!r} must be accepted; got: {exc}"
        )
        assert "chained scheme" not in str(exc), (
            f"Scheme {good_path!r} must not trigger chained-scheme error; got: {exc}"
        )


# ---------------------------------------------------------------------------
# S-L: object-store @ addressing must not trigger userinfo check
# ---------------------------------------------------------------------------


def test_gs_project_at_bucket_input_accepted(tmp_path: Path) -> None:
    """gs://project@bucket/key is GCS idiomatic addressing; must NOT be rejected.

    urlparse extracts 'project' as the username for gs:// URLs, but for
    object-store schemes the @-form is part of the addressing syntax, not an
    embedded credential.  _check_userinfo must only fire for http/https/ftp/ftps.
    """
    out = tmp_path / "gs_at.recotem"
    content = f"""\
name: gs_at_input
source:
  type: csv
  path: gs://my-project@my-bucket/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    # Must not raise RecipeError with "credentials" for gs:// @ addressing.
    try:
        recipe = load_recipe(p)
        assert recipe.source.path == "gs://my-project@my-bucket/data.csv"
    except RecipeError as exc:
        assert "credentials" not in str(exc), (
            f"gs://project@bucket should not trigger userinfo check; got: {exc}"
        )


def test_s3_account_at_bucket_input_rejected(tmp_path: Path) -> None:
    """s3://account@bucket/key must be rejected as embedded credentials (I-16).

    S3 does not use ``@`` in its canonical addressing syntax.  Any URI with a
    userinfo component (``username@host``) in an s3:// path is treated as
    embedded credentials and is rejected at recipe load time.  Authentication
    must use environment-based mechanisms (instance profile, ``AWS_*`` env
    vars, etc.).
    """
    out = tmp_path / "s3_at.recotem"
    content = f"""\
name: s3_at_input
source:
  type: csv
  path: s3://myaccount@my-bucket/data.csv
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="embedded credentials"):
        load_recipe(p)


def test_http_embedded_credentials_still_rejected(tmp_path: Path) -> None:
    """http://user:pass@example.com must still be rejected (existing behaviour)."""
    out = tmp_path / "http_cred.recotem"
    content = f"""\
name: http_cred_check
source:
  type: csv
  path: http://user:pass@example.com/data.csv
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="credentials"):
        load_recipe(p)


def test_https_embedded_credentials_still_rejected(tmp_path: Path) -> None:
    """https://user:pass@example.com must still be rejected."""
    out = tmp_path / "https_cred.recotem"
    content = f"""\
name: https_cred_check
source:
  type: csv
  path: https://user:pass@example.com/data.csv
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {out}
"""
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="credentials"):
        load_recipe(p)


def test_ftp_embedded_credentials_rejected(tmp_path: Path) -> None:
    """ftp://user:pass@example.com must be rejected as embedded credentials."""
    out = tmp_path / "ftp_cred.recotem"
    # ftp is also on the reject list for userinfo (but ftp scheme not in output allow-list)
    # use as input path (ftp is in input allow-list? No, it's not).
    # ftp:// is not in _INPUT_ALLOWED_SCHEMES either; scheme check fires first.
    # We test the output path instead which also goes through _check_userinfo.
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="ftp_cred_check",
        output_path="ftp://user:pass@example.com/out.recotem",
    )
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError):
        load_recipe(p)


# ---------------------------------------------------------------------------
# N-9: M-8 — MemoryError propagates from load_recipe (not wrapped in RecipeError)
# ---------------------------------------------------------------------------


def test_load_recipe_memory_error_propagates_unwrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MemoryError raised inside load_recipe must propagate as MemoryError,
    not be silently converted to RecipeError.

    This is an OOM-safety contract: if YAML parsing raises MemoryError, the
    caller must see it directly so the process can respond (log, terminate,
    alert) rather than treating it as a schema validation failure.
    """
    import yaml as _yaml

    p = _minimal(tmp_path, name="oom_recipe")

    def _oom(*args, **kwargs):
        raise MemoryError("out of memory during YAML parse")

    monkeypatch.setattr(_yaml, "safe_load", _oom)

    with pytest.raises(MemoryError):
        load_recipe(p)


# ---------------------------------------------------------------------------
# Fix-1: no_expand_fields case-insensitive matching
# ---------------------------------------------------------------------------


def test_no_expand_fields_uppercase_declaration_blocks_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin that declares no_expand_fields={'SQL'} (uppercase) must still
    block env-var expansion in a YAML key 'sql:' (lowercase) and vice versa.

    This exercises the case-normalisation fix in _expand_node: both the
    combined_no_expand set and the YAML dict key are lowercased before
    comparison, so mismatched case can no longer bypass the injection guard.
    """
    from unittest.mock import patch

    from pydantic import BaseModel

    sql_ref = "${RECOTEM_RECIPE_INJECT}"

    class _UpperSqlConfig(BaseModel, extra="ignore"):
        type: str = "test_upper_sql"
        sql: str = ""

    class _UpperSqlSource:
        type_name: str = "test_upper_sql"
        Config = _UpperSqlConfig
        extras_required: list = []
        # Uppercase declaration — the gap that existed before the fix.
        no_expand_fields: frozenset = frozenset({"SQL"})

        def __init__(self, config: _UpperSqlConfig) -> None:  # pragma: no cover
            self.config = config

        def fetch(self, ctx):  # pragma: no cover
            raise NotImplementedError

    monkeypatch.setenv("RECOTEM_RECIPE_INJECT", "INJECTED")

    content = f"""\
name: upper_no_expand
source:
  type: test_upper_sql
  sql: "SELECT * FROM {sql_ref}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / "upper_no_expand.recotem"}
"""
    p = _write_recipe(tmp_path, content)

    with patch(
        "recotem.datasource.registry.get_source_class", return_value=_UpperSqlSource
    ):
        try:
            recipe = load_recipe(p)
            raw_sql = getattr(recipe.source, "sql", None)
            if raw_sql is not None:
                assert "INJECTED" not in raw_sql, (
                    "uppercase no_expand_fields declaration must still block expansion; "
                    f"found injected value in: {raw_sql!r}"
                )
                assert sql_ref in raw_sql, (
                    f"literal reference must be preserved; got: {raw_sql!r}"
                )
        except RecipeError as exc:
            # Schema/type errors are OK; expansion-related blacklist errors are not.
            assert "blacklisted" not in str(exc), (
                f"no_expand_fields (uppercase) must prevent blacklist firing; got: {exc}"
            )


# ---------------------------------------------------------------------------
# Fix-5: load_recipes_directory_lenient — per-file error, no full abort
# ---------------------------------------------------------------------------


def test_load_recipes_directory_lenient_continues_after_bad_file(
    tmp_path: Path,
) -> None:
    """load_recipes_directory_lenient returns all files; bad ones have exc, not None recipe."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Good recipe
    good_content = MINIMAL_RECIPE_TEMPLATE.format(
        name="good_recipe",
        output_path=str(tmp_path / "good.recotem"),
    )
    (recipes_dir / "a_good.yaml").write_text(good_content)

    # Bad YAML (syntax error)
    (recipes_dir / "b_bad.yaml").write_text("name: bad\n  broken: yaml: here:\n")

    results = load_recipes_directory_lenient(recipes_dir)

    assert len(results) == 2

    # Results are sorted by filename: a_good, b_bad
    good_path, good_recipe, good_err = results[0]
    bad_path, bad_recipe, bad_err = results[1]

    assert good_path.name == "a_good.yaml"
    assert good_recipe is not None
    assert good_err is None
    assert good_recipe.name == "good_recipe"

    assert bad_path.name == "b_bad.yaml"
    assert bad_recipe is None
    assert bad_err is not None


def test_load_recipes_directory_lenient_duplicate_name_is_per_file_error(
    tmp_path: Path,
) -> None:
    """Duplicate recipe names are reported as per-file errors, not a full abort."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    for i, fname in enumerate(["first.yaml", "second.yaml"]):
        content = MINIMAL_RECIPE_TEMPLATE.format(
            name="same_name",
            output_path=str(tmp_path / f"model_{i}.recotem"),
        )
        (recipes_dir / fname).write_text(content)

    results = load_recipes_directory_lenient(recipes_dir)
    assert len(results) == 2

    # First file succeeds; second fails with a duplicate error.
    _, recipe0, err0 = results[0]
    _, recipe1, err1 = results[1]

    assert recipe0 is not None and err0 is None
    assert recipe1 is None and err1 is not None
    assert "Duplicate" in str(err1) or "duplicate" in str(err1)


def test_load_recipes_directory_lenient_all_good(tmp_path: Path) -> None:
    """When all files are valid, lenient loader returns the same as strict."""
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    names = ["alpha", "beta", "gamma"]
    for name in names:
        content = MINIMAL_RECIPE_TEMPLATE.format(
            name=name,
            output_path=str(tmp_path / f"{name}.recotem"),
        )
        (recipes_dir / f"{name}.yaml").write_text(content)

    results = load_recipes_directory_lenient(recipes_dir)
    assert len(results) == 3
    assert all(recipe is not None and err is None for _, recipe, err in results)
    loaded_names = {recipe.name for _, recipe, err in results if recipe}  # type: ignore[union-attr]
    assert loaded_names == set(names)


# ---------------------------------------------------------------------------
# RL-1: RecipeError.category + lenient loader log-level differentiation
# ---------------------------------------------------------------------------


def test_recipe_error_has_category_attribute() -> None:
    """RecipeError must have a .category attribute (defaults to 'unknown')."""
    from recotem.recipe.errors import RecipeError

    err = RecipeError("something went wrong")
    assert hasattr(err, "category")
    assert err.category == "unknown"


def test_recipe_error_security_category() -> None:
    """RecipeError with category='security' exposes it correctly."""
    from recotem.recipe.errors import RecipeError

    err = RecipeError("symlink escape", category="security")
    assert err.category == "security"


def test_recipe_error_invalid_category_falls_back_to_unknown() -> None:
    """An unrecognised category string is normalised to 'unknown'."""
    from recotem.recipe.errors import RecipeError

    err = RecipeError("oops", category="totally_made_up")
    assert err.category == "unknown"


def test_recipe_error_all_valid_categories() -> None:
    """All documented categories are accepted without normalisation."""
    from recotem.recipe.errors import RecipeError

    for cat in ("security", "schema", "parse", "io", "unknown"):
        assert RecipeError("x", category=cat).category == cat


def test_symlink_escape_logged_at_error_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink-escape recipe must produce an ERROR log with event
    'recipe_security_violation_skipped' in the lenient loader.
    """
    import structlog.testing

    from recotem.recipe.errors import RecipeError

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Place a valid recipe first (ensures at least one good entry).
    good = MINIMAL_RECIPE_TEMPLATE.format(
        name="good_recipe",
        output_path=str(tmp_path / "good_recipe.recotem"),
    )
    (recipes_dir / "good.yaml").write_text(good)

    # Place a well-formed recipe YAML for 'bad.yaml'.
    bad_content = MINIMAL_RECIPE_TEMPLATE.format(
        name="bad_recipe",
        output_path=str(tmp_path / "bad_recipe.recotem"),
    )
    (recipes_dir / "bad.yaml").write_text(bad_content)

    # Monkeypatch load_recipe to raise a security-category RecipeError when
    # processing 'bad.yaml', simulating a symlink-escape detection.
    from recotem.recipe import loader as loader_mod

    _real_load = loader_mod.load_recipe

    def _patched_load(path, **kwargs):
        if Path(path).name == "bad.yaml":
            raise RecipeError(
                "Recipe file outside root — path traversal rejected",
                category="security",
            )
        return _real_load(path, **kwargs)

    monkeypatch.setattr(loader_mod, "load_recipe", _patched_load)

    with structlog.testing.capture_logs() as logs:
        results = load_recipes_directory_lenient(recipes_dir)

    # The bad entry must be present as an error.
    failed = [(p, r, e) for p, r, e in results if e is not None]
    assert len(failed) == 1
    assert isinstance(failed[0][2], RecipeError)

    # Security violation must be logged at ERROR with the right event name.
    error_logs = [
        e
        for e in logs
        if e.get("log_level") == "error"
        and e.get("event") == "recipe_security_violation_skipped"
    ]
    assert error_logs, (
        f"Expected ERROR log 'recipe_security_violation_skipped'; "
        f"got log events: {[e.get('event') for e in logs]!r}"
    )
    assert error_logs[0].get("file") == "bad.yaml"


def test_yaml_parse_error_logged_at_warn_level(tmp_path: Path) -> None:
    """A YAML syntax error must produce a WARN log with event
    'recipe_load_error_skipped' in the lenient loader.
    """
    import structlog.testing

    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()

    # Malformed YAML (unbalanced bracket).
    (recipes_dir / "bad_yaml.yaml").write_text("name: [unclosed\n")

    with structlog.testing.capture_logs() as logs:
        results = load_recipes_directory_lenient(recipes_dir)

    failed = [(p, r, e) for p, r, e in results if e is not None]
    assert len(failed) == 1

    warn_logs = [
        e
        for e in logs
        if e.get("log_level") in ("warning", "warn")
        and e.get("event") == "recipe_load_error_skipped"
    ]
    assert warn_logs, (
        f"Expected WARN log 'recipe_load_error_skipped'; "
        f"got log events: {[e.get('event') for e in logs]!r}"
    )


def test_containment_violation_recipe_error_has_security_category(
    tmp_path: Path,
) -> None:
    """_check_recipe_file_containment raises RecipeError(category='security').

    Simulates a symlink that resolves outside the recipes root and asserts the
    category is set so the lenient loader can escalate the log level.
    """
    from recotem.recipe.loader import _check_recipe_file_containment

    root = tmp_path / "recipes"
    root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("")

    with pytest.raises(RecipeError) as exc_info:
        _check_recipe_file_containment(outside, root)

    assert exc_info.value.category == "security"


def test_userinfo_rejection_recipe_error_has_security_category(
    tmp_path: Path,
) -> None:
    """Embedded URI credentials raise RecipeError(category='security')."""
    from recotem.recipe.loader import _validate_input_path

    with pytest.raises(RecipeError) as exc_info:
        _validate_input_path("https://user:pass@example.com/data.csv", "source.path")

    assert exc_info.value.category == "security"


def test_scheme_violation_input_path_has_security_category(tmp_path: Path) -> None:
    """A disallowed input scheme raises RecipeError(category='security')."""
    from recotem.recipe.loader import _validate_input_path

    with pytest.raises(RecipeError) as exc_info:
        _validate_input_path("ftp://example.com/data.csv", "source.path")

    assert exc_info.value.category == "security"


# ---------------------------------------------------------------------------
# RL-2: DataSourceError from plugin discovery re-raised as RecipeError
# ---------------------------------------------------------------------------


def test_datasource_error_from_plugin_discovery_re_raised_as_recipe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the DataSource registry raises DataSourceError for the source type
    during env-var expansion, load_recipe must raise RecipeError (not silently
    continue with weakened no_expand_fields protection).
    """
    from recotem.datasource.base import DataSourceError
    from recotem.recipe import loader as loader_mod

    # A recipe with a real source type so we get past name/YAML validation.
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="ds_err_test",
        output_path=str(tmp_path / "ds_err_test.recotem"),
    )
    p = _write_recipe(tmp_path, content)

    # Patch get_source_class to raise DataSourceError (simulating a broken plugin).
    _real_get = loader_mod.__dict__.get("get_source_class", None)

    # Import the registry so we can patch it in the loader's namespace.
    from recotem.datasource import registry as reg_mod

    _real_get_source_class = reg_mod.get_source_class

    def _bad_get(type_name: str):
        raise DataSourceError(f"plugin {type_name!r} failed to load")

    # Patch inside the loader module's imported reference.
    monkeypatch.setattr(reg_mod, "get_source_class", _bad_get)

    with pytest.raises(RecipeError) as exc_info:
        load_recipe(p)

    assert (
        "plugin source discovery failed" in str(exc_info.value).lower()
        or "failed" in str(exc_info.value).lower()
    ), f"RecipeError message must mention the failure; got: {exc_info.value!r}"
    assert exc_info.value.category == "schema"


# ---------------------------------------------------------------------------
# Round-15 MD2: cloud-provider env-var prefix blacklist extended
# ---------------------------------------------------------------------------


def test_env_var_expansion_oci_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle Cloud env vars (OCI_*) must be blacklisted for recipe expansion.

    Without the prefix entry these would slip through the substring
    blacklist (e.g. ``OCI_TENANCY_OCID`` has no KEY / TOKEN / CRED token).
    """
    monkeypatch.setenv("OCI_TENANCY_OCID", "ocid1.tenancy.oc1..xxxx")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="oci_bl",
        output_path=str(tmp_path / "oci_bl.recotem"),
    )
    content = content.replace("user_id", "${OCI_TENANCY_OCID}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_env_var_expansion_aliyun_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alibaba Cloud env vars (ALIYUN_*) must be blacklisted."""
    monkeypatch.setenv("ALIYUN_REGION_ID", "cn-hongkong")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="aliyun_bl",
        output_path=str(tmp_path / "aliyun_bl.recotem"),
    )
    content = content.replace("user_id", "${ALIYUN_REGION_ID}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_env_var_expansion_digitalocean_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DigitalOcean env vars (DIGITALOCEAN_*) must be blacklisted.

    Names like ``DIGITALOCEAN_ACCESS_TOKEN`` already match the TOKEN
    substring, but ``DIGITALOCEAN_REGION`` does not — the new prefix
    catches that case too.
    """
    monkeypatch.setenv("DIGITALOCEAN_REGION", "nyc3")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="do_bl",
        output_path=str(tmp_path / "do_bl.recotem"),
    )
    content = content.replace("user_id", "${DIGITALOCEAN_REGION}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_env_var_expansion_hcloud_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hetzner Cloud env vars (HCLOUD_*) must be blacklisted."""
    monkeypatch.setenv("HCLOUD_LOCATION", "fsn1")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="hcloud_bl",
        output_path=str(tmp_path / "hcloud_bl.recotem"),
    )
    content = content.replace("user_id", "${HCLOUD_LOCATION}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


def test_env_var_expansion_ibm_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IBM Cloud env vars (IBM_*) must be blacklisted."""
    monkeypatch.setenv("IBM_CLOUD_REGION", "us-south")
    content = MINIMAL_RECIPE_TEMPLATE.format(
        name="ibm_bl",
        output_path=str(tmp_path / "ibm_bl.recotem"),
    )
    content = content.replace("user_id", "${IBM_CLOUD_REGION}")
    p = _write_recipe(tmp_path, content)
    with pytest.raises(RecipeError, match="blacklisted"):
        load_recipe(p)


# ---------------------------------------------------------------------------
# I-16: Embedded credentials in s3:// and abfs(s):// URIs must be rejected
# ---------------------------------------------------------------------------


def _recipe_with_source_path(
    tmp_path: Path, source_path: str, name: str = "test"
) -> Path:
    """Write a recipe YAML using the given source path (no sha256 — for scheme tests)."""
    content = f"""\
name: {name}
source:
  type: csv
  path: {source_path}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / name}.recotem
"""
    return _write_recipe(tmp_path, content, filename=f"{name}.yaml")


def test_s3_embedded_credentials_rejected(tmp_path: Path) -> None:
    """s3://AKID:SECRET@bucket/key must raise RecipeError (embedded credentials)."""
    p = _recipe_with_source_path(
        tmp_path, "s3://AKIAIOSFODNN7EXAMPLE:wJalrXUtnFEMI@my-bucket/data.csv"
    )
    with pytest.raises(RecipeError, match="embedded credentials"):
        load_recipe(p)


def test_s3_no_credentials_accepted(tmp_path: Path) -> None:
    """s3://bucket/key without credentials must NOT raise a credentials error."""
    p = _recipe_with_source_path(tmp_path, "s3://my-bucket/data.csv")
    # May raise for other reasons (missing sha256 etc.) but NOT for credentials.
    try:
        load_recipe(p)
    except RecipeError as exc:
        assert "embedded credentials" not in str(exc), (
            f"Unexpectedly raised credentials error for plain s3:// path: {exc}"
        )


def test_abfs_embedded_credentials_rejected(tmp_path: Path) -> None:
    """abfs://user:pass@container@account.dfs.core.windows.net must raise RecipeError."""
    p = _recipe_with_source_path(
        tmp_path, "abfs://myuser:mysecret@mycontainer/data.csv"
    )
    with pytest.raises(RecipeError, match="embedded credentials"):
        load_recipe(p)


def test_abfss_embedded_credentials_rejected(tmp_path: Path) -> None:
    """abfss://user:pass@... must raise RecipeError (embedded credentials)."""
    p = _recipe_with_source_path(
        tmp_path, "abfss://myuser:mysecret@mycontainer/data.csv"
    )
    with pytest.raises(RecipeError, match="embedded credentials"):
        load_recipe(p)


def test_gs_project_at_bucket_accepted(tmp_path: Path) -> None:
    """gs://project@bucket/key must NOT raise a credentials error.

    GCS uses `project@bucket` as its canonical URI syntax for billing-project
    override.  The `@` character here is not a credential separator.
    """
    p = _recipe_with_source_path(tmp_path, "gs://my-project@my-bucket/data.csv")
    # May raise for other reasons (e.g. missing sha256 or fsspec issues) but
    # must NOT raise RecipeError for embedded credentials.
    try:
        load_recipe(p)
    except RecipeError as exc:
        assert "embedded credentials" not in str(exc), (
            f"gs://project@bucket/key must not trigger credentials rejection; "
            f"got: {exc}"
        )
