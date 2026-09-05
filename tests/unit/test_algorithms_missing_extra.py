"""Guards that a missing optional extra does not read as a typo.

``BPRFM`` is a valid, documented alias. It resolves cleanly through
``resolve_algorithm_name`` and then dies in ``get_recommender_cls`` with a
message that blames irspack, because irspack reports "gated behind an optional
dependency" and "not a recommender at all" the same way: by not exporting the
class. Told only that "irspack does not know recommender class
'BPRFMRecommender'", an operator searches irspack's issue tracker; the actual
remedy is ``pip install 'recotem[bprfm]'``.

The suite normally runs with every extra installed, so absence is simulated by
making irspack's own lookup fail for that one class -- exactly what it does
when ``import lightfm`` fails inside ``irspack/recommenders/bpr.py``.
"""

from __future__ import annotations

import pytest

from recotem.training import algorithms
from recotem.training.errors import UnknownAlgorithmError


@pytest.fixture
def without_bprfm(monkeypatch: pytest.MonkeyPatch):
    """Make irspack behave as it does when ``lightfm`` is not importable."""
    real = algorithms.get_recommender_class

    def fake(name: str):
        if name == "BPRFMRecommender":
            raise KeyError(name)
        return real(name)

    monkeypatch.setattr(algorithms, "get_recommender_class", fake)
    algorithms.constructible_class_names.cache_clear()
    yield
    algorithms.constructible_class_names.cache_clear()


def test_missing_extra_names_the_extra_and_the_install_command(without_bprfm) -> None:
    with pytest.raises(UnknownAlgorithmError) as exc:
        algorithms.get_recommender_cls("BPRFMRecommender")

    message = str(exc.value)
    assert "bprfm" in message
    assert "pip install 'recotem[bprfm]'" in message
    # The old message blamed irspack for what is a packaging state.
    assert "irspack does not know" not in message


def test_a_real_unknown_class_still_says_irspack_does_not_know_it(
    without_bprfm,
) -> None:
    """Only recotem's own gated algorithms get the extras remedy."""
    with pytest.raises(UnknownAlgorithmError) as exc:
        algorithms.get_recommender_cls("NotARecommenderAtAll")

    message = str(exc.value)
    assert "irspack does not know" in message
    assert "pip install" not in message


def test_every_gated_class_is_a_supported_class_name() -> None:
    """A typo in the map would advertise an extra for an algorithm we do not ship."""
    assert set(algorithms._GATED_CLASS_EXTRAS) <= algorithms.SUPPORTED_CLASS_NAMES


def test_gated_class_is_reachable_by_its_public_alias() -> None:
    """The remedy is only useful if the alias an operator writes maps here."""
    assert algorithms.resolve_algorithm_name("BPRFM") in algorithms._GATED_CLASS_EXTRAS


def test_availability_is_asked_of_irspack_not_read_from_the_map(without_bprfm) -> None:
    """The map must not become a second, drifting source of truth.

    ``constructible_class_names`` decides availability by trying to construct,
    so a gated class that IS importable stays listed and the map only ever
    supplies wording.
    """
    assert "BPRFMRecommender" not in algorithms.constructible_class_names()
    algorithms.constructible_class_names.cache_clear()
    # With the real lookup restored by the fixture teardown ordering, the class
    # is present in this all-extras environment.
    assert "IALSRecommender" in algorithms.constructible_class_names()
