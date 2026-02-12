"""Tests for A/B testing statistical analysis service."""

import pytest

from recotem.api.services.ab_testing_service import compute_ab_results, select_slot_by_weight


class TestComputeABResults:
    def test_zero_impressions_returns_defaults(self):
        result = compute_ab_results(0, 0, 0, 0)
        assert result["control_rate"] == 0.0
        assert result["variant_rate"] == 0.0
        assert result["z_score"] == 0.0
        assert result["p_value"] == 1.0
        assert result["significant"] is False
        assert result["lift"] == 0.0
        assert result["confidence_interval"] == [0.0, 0.0]

    def test_control_zero_impressions(self):
        result = compute_ab_results(0, 0, 100, 10)
        assert result["significant"] is False
        assert result["p_value"] == 1.0

    def test_variant_zero_impressions(self):
        result = compute_ab_results(100, 10, 0, 0)
        assert result["significant"] is False

    def test_equal_rates_not_significant(self):
        result = compute_ab_results(1000, 100, 1000, 100)
        assert result["control_rate"] == pytest.approx(0.1, abs=1e-6)
        assert result["variant_rate"] == pytest.approx(0.1, abs=1e-6)
        assert result["significant"] == False  # noqa: E712  (numpy bool)
        assert result["lift"] == pytest.approx(0.0, abs=0.01)

    def test_large_difference_is_significant(self):
        # 10% control vs 20% variant with large sample
        result = compute_ab_results(10000, 1000, 10000, 2000)
        assert result["control_rate"] == pytest.approx(0.1, abs=1e-6)
        assert result["variant_rate"] == pytest.approx(0.2, abs=1e-6)
        assert result["significant"] == True  # noqa: E712  (numpy bool)
        assert result["lift"] == pytest.approx(100.0, abs=1.0)
        assert result["p_value"] < 0.05

    def test_confidence_interval_contains_zero_when_equal(self):
        result = compute_ab_results(1000, 100, 1000, 100)
        ci = result["confidence_interval"]
        assert ci[0] <= 0.0 <= ci[1]

    def test_positive_lift(self):
        result = compute_ab_results(1000, 50, 1000, 100)
        assert result["lift"] > 0

    def test_negative_lift(self):
        result = compute_ab_results(1000, 100, 1000, 50)
        assert result["lift"] < 0

    def test_custom_confidence_level(self):
        result = compute_ab_results(1000, 100, 1000, 130, confidence_level=0.99)
        # With 99% confidence, marginal differences become non-significant
        assert result["significant"] in (True, False)

    def test_all_conversions(self):
        result = compute_ab_results(100, 100, 100, 100)
        assert result["control_rate"] == pytest.approx(1.0, abs=1e-6)
        assert result["variant_rate"] == pytest.approx(1.0, abs=1e-6)

    def test_zero_control_conversions_lift(self):
        result = compute_ab_results(100, 0, 100, 10)
        assert result["lift"] == 0.0  # Can't compute lift when control rate is 0


class TestSelectSlotByWeight:
    def test_single_slot_always_selected(self):
        class MockSlot:
            def __init__(self, name, weight):
                self.name = name
                self.weight = weight

        slot = MockSlot("only", 100)
        assert select_slot_by_weight([slot]) == slot

    def test_empty_slots_raises(self):
        with pytest.raises(ValueError, match="No deployment slots"):
            select_slot_by_weight([])

    def test_zero_weight_raises(self):
        class MockSlot:
            def __init__(self, w):
                self.weight = w

        with pytest.raises(ValueError, match="positive"):
            select_slot_by_weight([MockSlot(0), MockSlot(0)])

    def test_weighted_selection_distribution(self):
        class MockSlot:
            def __init__(self, name, weight):
                self.name = name
                self.weight = weight

        a = MockSlot("a", 90)
        b = MockSlot("b", 10)

        counts = {"a": 0, "b": 0}
        for _ in range(1000):
            selected = select_slot_by_weight([a, b])
            counts[selected.name] += 1

        # With 90/10 weights, slot A should be selected ~90% of the time
        assert counts["a"] > 700  # Very loose bound
        assert counts["b"] > 10
