"""Guards that ``recotem validate`` actually checks the ``item_metadata`` block.

``validate`` is documented as the fast way to catch recipe problems before
launching ``train``. It probed ``source`` and ``features.*.source`` but never
``item_metadata``, which is not a DataSource and so was absent from the probe
loop. That made it the one block that can be wrong in a recipe which validates
AND trains to a signed artifact, both exit 0 -- the failure first appearing when
``serve`` starts, or, worse, on a hot-swap in production where the previous
model keeps serving and only ``/v1/health`` going degraded says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from recotem.cli import app

runner = CliRunner()

# 20 users x 10 distinct items each, from a 15-item catalog.  The per-user
# depth is load-bearing: the `random` split holds out
# floor(n_distinct * heldout_ratio) PER USER, so at 0.2 a user needs at least 5
# distinct items or training fails on an empty held-out set before it can
# demonstrate anything about item_metadata.
_INTERACTIONS = "user_id,item_id\n" + "".join(
    f"u{u},it{(u + k) % 15}\n" for u in range(20) for k in range(10)
)
_METADATA = "item_id,title,category\n" + "".join(
    f"it{i},Title {i},cat{i % 3}\n" for i in range(15)
)


def _write_recipe(
    tmp_path: Path,
    *,
    metadata_path: str,
    fields: str = "[title, category]",
    item_id_column: str = "item_id",
    metadata_body: str = _METADATA,
) -> Path:
    (tmp_path / "inter.csv").write_text(_INTERACTIONS)
    meta = tmp_path / "meta.csv"
    if metadata_body is not None:
        meta.write_text(metadata_body)

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        f"""
name: metacheck
source:
  type: csv
  path: {tmp_path / "inter.csv"}
  dtype: {{user_id: str, item_id: str}}
schema: {{user_column: user_id, item_column: item_id}}
item_metadata:
  type: csv
  path: {metadata_path}
  fields: {fields}
  item_id_column: {item_id_column}
cleansing:
  {{drop_null_ids: true, dedup: keep_last, min_rows: 10, min_users: 5,
    min_items: 5}}
training:
  algorithms: [TopPop]
  metric: ndcg
  cutoff: 5
  n_trials: 2
  split: {{scheme: random, heldout_ratio: 0.2, seed: 42}}
output:
  path: {tmp_path / "out.recotem"}
  versioning: always_overwrite
"""
    )
    return recipe


def test_valid_item_metadata_passes_and_is_reported(tmp_path: Path) -> None:
    recipe = _write_recipe(tmp_path, metadata_path=str(tmp_path / "meta.csv"))
    result = runner.invoke(app, ["validate", str(recipe)])
    out = result.stdout + result.stderr

    assert result.exit_code == 0, out
    # Silence would be indistinguishable from the old not-checked-at-all state.
    assert "Item metadata: OK" in out, out
    assert "15 rows" in out, out


def test_missing_metadata_file_fails_validate(tmp_path: Path) -> None:
    recipe = _write_recipe(tmp_path, metadata_path=str(tmp_path / "absent.csv"))
    result = runner.invoke(app, ["validate", str(recipe)])
    out = result.stdout + result.stderr

    assert result.exit_code == 3, f"expected exit 3, got {result.exit_code}: {out}"
    assert "Item metadata check failed" in out, out
    assert "absent.csv" in out, out


def test_wrong_item_id_column_fails_validate(tmp_path: Path) -> None:
    recipe = _write_recipe(
        tmp_path,
        metadata_path=str(tmp_path / "meta.csv"),
        fields="[title]",
        item_id_column="product_id",
    )
    result = runner.invoke(app, ["validate", str(recipe)])
    out = result.stdout + result.stderr

    assert result.exit_code == 3, out
    assert "product_id" in out, out
    # The operator must be able to see what the file DOES have.
    assert "available columns" in out, out


def test_missing_declared_field_fails_validate(tmp_path: Path) -> None:
    recipe = _write_recipe(
        tmp_path, metadata_path=str(tmp_path / "meta.csv"), fields="[nonexistent]"
    )
    result = runner.invoke(app, ["validate", str(recipe)])
    out = result.stdout + result.stderr

    assert result.exit_code == 3, out
    assert "nonexistent" in out, out


def test_recipe_without_item_metadata_is_unaffected(tmp_path: Path) -> None:
    """The block is optional; absence must not add a check or a line."""
    (tmp_path / "inter.csv").write_text(_INTERACTIONS)
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        f"""
name: nometa
source:
  type: csv
  path: {tmp_path / "inter.csv"}
  dtype: {{user_id: str, item_id: str}}
schema: {{user_column: user_id, item_column: item_id}}
cleansing:
  {{drop_null_ids: true, dedup: keep_last, min_rows: 10, min_users: 5,
    min_items: 5}}
training:
  algorithms: [TopPop]
  metric: ndcg
  cutoff: 5
  n_trials: 2
  split: {{scheme: random, heldout_ratio: 0.2, seed: 42}}
output:
  path: {tmp_path / "out.recotem"}
  versioning: always_overwrite
"""
    )
    result = runner.invoke(app, ["validate", str(recipe)])
    out = result.stdout + result.stderr

    assert result.exit_code == 0, out
    assert "Item metadata" not in out, out


@pytest.mark.parametrize(
    ("fields", "item_id_column"),
    [("[title]", "product_id"), ("[nonexistent]", "item_id")],
)
def test_these_recipes_still_train_fine_which_is_why_validate_must_catch_them(
    tmp_path: Path, fields: str, item_id_column: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The train path is genuinely blind here -- that is the whole hazard.

    ``item_metadata`` is a serve-time join; ``train`` never reads it. So a
    recipe with a broken block produces a signed artifact and exit 0, and
    without this check nothing between the operator and production says
    otherwise. Pinning it keeps anyone from "fixing" the gap by making train
    fail instead, which would break the batch/serve split.
    """
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", "t:" + "ab" * 32)
    recipe = _write_recipe(
        tmp_path,
        metadata_path=str(tmp_path / "meta.csv"),
        fields=fields,
        item_id_column=item_id_column,
    )

    assert runner.invoke(app, ["validate", str(recipe)]).exit_code == 3

    train = runner.invoke(app, ["train", str(recipe)])
    assert train.exit_code == 0, train.stdout + train.stderr
    assert (tmp_path / "out.recotem").exists()
