"""Business-oriented per-fuel-type LightGBM decision threshold selection.

Never picks a threshold by maximum F1 alone. A candidate threshold must first clear
three floors/caps derived from how the signal would actually be used: it must not be
so conservative that recall becomes unusable, so trigger-happy that it causes alert
fatigue, or so late that its warnings carry no useful lead time. Only among the
thresholds that clear all three is F1 used as a tiebreaker. See
docs/threshold-calibration.md for the chosen floor/cap values and their justification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThresholdCandidate:
    """Validation-set metrics for one candidate decision threshold."""

    threshold: float
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    alerts: int
    false_alarms_per_market_month: float
    average_lead_time_days: float | None


@dataclass(frozen=True)
class ThresholdSelection:
    """Result of applying the business rule to a candidate grid."""

    chosen_threshold: float | None
    candidate: ThresholdCandidate | None
    reason: str


def select_threshold(
    candidates: list[ThresholdCandidate],
    min_recall: float,
    max_false_alarms_per_market_month: float,
    min_avg_lead_time_days: float,
) -> ThresholdSelection:
    """Pick the qualifying threshold with the highest F1.

    Qualifying means all three hold simultaneously:
      - recall >= min_recall (prevents unusably low recall)
      - false_alarms_per_market_month <= max_false_alarms_per_market_month (limits
        alert fatigue)
      - average_lead_time_days is not None and >= min_avg_lead_time_days (preserves
        useful lead time - a threshold with no matched warnings at all has no lead
        time to measure and is rejected here, not silently treated as acceptable)

    Returns a selection with `chosen_threshold=None` and an explanatory reason when
    no candidate qualifies - callers must not silently fall back to an arbitrary
    threshold in that case.
    """
    qualifying = [
        c
        for c in candidates
        if c.recall >= min_recall
        and c.false_alarms_per_market_month <= max_false_alarms_per_market_month
        and c.average_lead_time_days is not None
        and c.average_lead_time_days >= min_avg_lead_time_days
    ]
    if not qualifying:
        return ThresholdSelection(
            chosen_threshold=None, candidate=None, reason="no_threshold_satisfies_constraints"
        )
    best = max(qualifying, key=lambda c: c.f1)
    return ThresholdSelection(
        chosen_threshold=best.threshold, candidate=best, reason="highest_f1_among_qualifying"
    )
