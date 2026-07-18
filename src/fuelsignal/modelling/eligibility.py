"""Model-eligibility decision logic for station x fuel_type price series.

Pure Python so the exclusion rule is unit-testable independent of the live SQL that
computes the underlying per-series statistics (scripts/run_model_eligibility.py).
See docs/model-eligibility.md for the full empirical basis for the default thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EligibilityDecision:
    """Whether one station x fuel_type series may be used for model training."""

    is_eligible: bool
    exclusion_reason: str | None


def evaluate_eligibility(
    total_observations: int,
    extreme_change_rate: float,
    min_observations: int,
    max_extreme_change_rate: float,
) -> EligibilityDecision:
    """Decide eligibility from two independent, additive criteria.

    Args:
        total_observations: Count of gold_station_daily_market rows for this series.
        extreme_change_rate: Fraction of gold_market_cycle_features rows for this
            series with an implausible |rolling_14d_price_change_cpl| > 100 cpl swing.
        min_observations: Minimum total_observations required.
        max_extreme_change_rate: Maximum tolerated extreme_change_rate.

    Both reasons are reported (joined with "+") when a series fails on both criteria,
    so the audit table never hides a second problem behind the first one found.
    """
    reasons = []
    if total_observations < min_observations:
        reasons.append("insufficient_observations")
    if extreme_change_rate > max_extreme_change_rate:
        reasons.append("extreme_price_volatility")
    if reasons:
        return EligibilityDecision(is_eligible=False, exclusion_reason="+".join(reasons))
    return EligibilityDecision(is_eligible=True, exclusion_reason=None)
