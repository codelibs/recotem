"""Integration test: third-party plugin source → recipe YAML → train.

Regression guard for the ``source.type`` discriminator contract.

``examples/plugins/echo-source`` shipped a ``Config`` without a ``type`` field.
Every unit test passed — the class satisfied ``validate_plugin_contract`` and
``fetch()`` returned a DataFrame — but no test ever loaded a recipe *through
the YAML loader* and handed it to the training pipeline.  Along that path
``Config.model_validate(raw_source)`` runs with pydantic's default
``extra="ignore"``, which silently drops the YAML ``type:`` key, and
``recotem.training.pipeline._fetch_data`` then reads ``getattr(config, "type")``
back as ``None`` and aborts with "Recipe source has no discriminator 'type'
field." (exit 2).

These tests therefore exercise the full recipe YAML → source resolution →
train path rather than the plugin class in isolation.

NOTE: unpickle_payload uses the project's SafeUnpickler with a hand-enumerated
FQCN allow-list. Pickle is required because irspack's IDMappedRecommender
carries scipy sparse matrices and numpy arrays; see tests/conftest.py.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Deterministic signing key — same format used in tests/conftest.py.
_ACTIVE_KEY_HEX = "aa" * 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _echo_entry_point() -> MagicMock:
    """Entry point that resolves to the shipped echo-source example plugin.

    ``tests/conftest.py`` puts ``examples/plugins/echo-source/src`` on sys.path,
    so the plugin imports without a separate install; only the entry-point
    registration has to be simulated.
    """
    from recotem_echo.source import EchoSource

    ep = MagicMock()
    ep.name = "echo"
    ep.value = "recotem_echo:EchoSource"
    ep.load.return_value = EchoSource
    return ep


def _write_echo_recipe(tmp_path: Path, artifact_path: str) -> Path:
    """Write a recipe YAML whose source is the third-party ``echo`` plugin."""
    yaml_path = tmp_path / "echo_recipe.yaml"
    yaml_path.write_text(
        textwrap.dedent(f"""\
            name: echo_plugin_example
            source:
              type: echo
              n_users: 20
              n_items: 50
              n_rows: 200
              seed: 42
            schema:
              user_column: user_id
              item_column: item_id
            training:
              algorithms: [TopPop]
              n_trials: 1
              cutoff: 10
              split:
                scheme: random
                heldout_ratio: 0.2
                seed: 42
            output:
              path: {artifact_path}
              versioning: always_overwrite
            """)
    )
    return yaml_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_plugin_recipe_yaml_preserves_type_discriminator(tmp_path: Path) -> None:
    """load_recipe must keep ``source.type`` readable on the typed Config.

    This is the precise assertion the old test suite was missing: the plugin
    class was fine, but the value that reached the training pipeline had lost
    its discriminator.
    """
    from recotem.datasource import registry
    from recotem.recipe.loader import load_recipe

    artifact_path = str(tmp_path / "echo.recotem")
    yaml_path = _write_echo_recipe(tmp_path, artifact_path)

    with patch(
        "recotem.datasource.registry.entry_points",
        return_value=[_echo_entry_point()],
    ):
        registry.clear_registry_cache()
        recipe = load_recipe(str(yaml_path))

    assert getattr(recipe.source, "type", None) == "echo", (
        "source.type was dropped by Config.model_validate — the plugin Config "
        'must declare \'type: Literal["echo"] = "echo"\''
    )


def test_plugin_recipe_trains_and_writes_artifact(tmp_path: Path) -> None:
    """Full path: recipe YAML → echo plugin fetch → train → signed artifact."""
    from recotem.artifact.io import read_artifact
    from recotem.artifact.signing import KeyRing, unpickle_payload
    from recotem.datasource import registry
    from recotem.recipe.loader import load_recipe
    from recotem.training._compat import IDMappedRecommender
    from recotem.training.pipeline import run_training

    artifact_path = str(tmp_path / "echo.recotem")
    yaml_path = _write_echo_recipe(tmp_path, artifact_path)

    kr = KeyRing(f"active:{_ACTIVE_KEY_HEX}")

    with patch(
        "recotem.datasource.registry.entry_points",
        return_value=[_echo_entry_point()],
    ):
        registry.clear_registry_cache()
        recipe = load_recipe(str(yaml_path))
        result = run_training(
            recipe,
            key_ring=kr,
            signing_key="active",
            no_lock=True,
            dev_allow_unsigned=False,
            quiet=True,
        )

    assert result is not None, "run_training returned None unexpectedly"
    assert result.best_class is not None

    written = Path(result.artifact_path)
    assert written.exists(), f"artifact not found at {written}"

    header, payload_bytes = read_artifact(str(written), kr)
    recommender = unpickle_payload(payload_bytes)  # noqa: S301
    assert isinstance(recommender, IDMappedRecommender)
    # EchoSource emits n_users=20 distinct users and draws items from n_items=50.
    assert len(recommender.user_ids) == 20
    assert len(recommender.item_ids) > 0


def test_plugin_config_without_type_is_rejected_at_discovery() -> None:
    """A Config missing the discriminator must fail at plugin discovery.

    Before this guard the plugin registered cleanly and only blew up later,
    mid-training, with an error that named neither the plugin nor the fix.
    """
    from pydantic import BaseModel

    from recotem.datasource import registry
    from recotem.datasource.base import DataSourceError

    class NoTypeConfig(BaseModel):
        n_rows: int = 10

    class BrokenSource:
        type_name = "broken"
        Config = NoTypeConfig
        extras_required: list[str] = []
        no_expand_fields = frozenset()

        def fetch(self, ctx): ...

    ep = MagicMock()
    ep.name = "broken"
    ep.value = "broken_pkg:BrokenSource"
    ep.load.return_value = BrokenSource

    with patch("recotem.datasource.registry.entry_points", return_value=[ep]):
        registry.clear_registry_cache()
        with pytest.raises(DataSourceError, match="required 'type' discriminator"):
            registry.get_source_types()
