"""A/B testing statistical analysis service."""

import math
import random

from scipy import stats


def compute_ab_results(
    control_impressions: int,
    control_conversions: int,
    variant_impressions: int,
    variant_conversions: int,
    confidence_level: float = 0.95,
) -> dict:
    """Compute A/B test results using two-proportion Z-test.

    Returns a dict with z_score, p_value, significant, lift, confidence_interval.
    """
    if control_impressions == 0 or variant_impressions == 0:
        return {
            "control_rate": 0.0,
            "variant_rate": 0.0,
            "z_score": 0.0,
            "p_value": 1.0,
            "significant": False,
            "lift": 0.0,
            "confidence_interval": [0.0, 0.0],
        }

    p_control = control_conversions / control_impressions
    p_variant = variant_conversions / variant_impressions

    # Pooled proportion
    p_pool = (control_conversions + variant_conversions) / (
        control_impressions + variant_impressions
    )

    # Standard error
    se = (
        math.sqrt(
            p_pool * (1 - p_pool) * (1 / control_impressions + 1 / variant_impressions)
        )
        if p_pool > 0 and p_pool < 1
        else 0.0
    )

    # Z-score
    z_score = (p_variant - p_control) / se if se > 0 else 0.0

    # Two-tailed p-value
    p_value = 2 * (1 - stats.norm.cdf(abs(z_score)))

    # Significance
    alpha = 1 - confidence_level
    significant = p_value < alpha

    # Lift (relative improvement)
    lift = ((p_variant - p_control) / p_control * 100) if p_control > 0 else 0.0

    # Confidence interval for the difference
    z_crit = stats.norm.ppf(1 - alpha / 2)
    se_diff = (
        math.sqrt(
            p_control * (1 - p_control) / control_impressions
            + p_variant * (1 - p_variant) / variant_impressions
        )
        if control_impressions > 0 and variant_impressions > 0
        else 0.0
    )
    diff = p_variant - p_control
    ci_lower = diff - z_crit * se_diff
    ci_upper = diff + z_crit * se_diff

    return {
        "control_rate": round(p_control, 6),
        "variant_rate": round(p_variant, 6),
        "z_score": round(z_score, 4),
        "p_value": round(p_value, 6),
        "significant": significant,
        "lift": round(lift, 2),
        "confidence_interval": [round(ci_lower, 6), round(ci_upper, 6)],
    }


def select_slot_by_weight(slots):
    """Select a deployment slot using weighted random selection.

    Args:
        slots: QuerySet or list of DeploymentSlot instances (should be active).

    Returns:
        A single DeploymentSlot selected proportionally to its weight.

    Raises:
        ValueError: If no active slots with positive weight are available.
    """
    slot_list = list(slots)
    if not slot_list:
        raise ValueError("No deployment slots available for selection.")

    weights = [s.weight for s in slot_list]
    if sum(weights) <= 0:
        raise ValueError("Total weight of deployment slots must be positive.")

    return random.choices(slot_list, weights=weights, k=1)[0]
