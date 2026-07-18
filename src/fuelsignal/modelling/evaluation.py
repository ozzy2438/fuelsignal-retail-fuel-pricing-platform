"""Market-day aggregation and lead-time/false-alarm evaluation for jump predictions.

Precision/recall/F1/PR-AUC are computed at the natural station-day-fuel row grain (the
model's actual prediction unit - see docs/model-eligibility.md). False-alarm count and
average warning lead time are inherently market-wide concepts (a repricing decision is
made once per market per day, not once per station), so they are computed after
aggregating station-day predictions to one decision per (fuel_type, market_date) via
majority vote.
"""

from __future__ import annotations

from dataclasses import dataclass


def aggregate_to_market_day(station_day_predicted_positive: list[bool]) -> bool:
    """Majority-vote aggregation of station-day predictions to one market-day decision.

    Empty input (no stations reported that day) is treated as no warning, never a
    guessed positive.
    """
    if not station_day_predicted_positive:
        return False
    positive_count = sum(station_day_predicted_positive)
    return positive_count / len(station_day_predicted_positive) >= 0.5


@dataclass(frozen=True)
class LeadTimeResult:
    """Warning/false-alarm/lead-time summary for one ordered market-day series."""

    warning_count: int
    false_alarm_count: int
    matched_jump_count: int
    average_lead_time_days: float | None


def evaluate_market_day_warnings(
    predicted_positive: list[bool],
    actual_jump_today: list[bool],
    max_lead_days: int = 2,
) -> LeadTimeResult:
    """Score an ordered (by date, no gaps assumed) series of market-day warnings.

    For each date the model raised a warning (`predicted_positive[i]` is True), look
    forward up to `max_lead_days` days for an actual jump. A warning with no jump in
    that window is a false alarm; a warning matched to a jump contributes its lead
    time (the number of days between the warning and the jump) to the average. Each
    warning matches at most one jump (the earliest one within the window).
    """
    n = len(predicted_positive)
    warning_count = 0
    false_alarms = 0
    lead_times: list[int] = []
    matched_jump_indices: set[int] = set()
    for i in range(n):
        if not predicted_positive[i]:
            continue
        warning_count += 1
        matched = False
        for offset in range(1, max_lead_days + 1):
            j = i + offset
            if j < n and actual_jump_today[j]:
                lead_times.append(offset)
                matched_jump_indices.add(j)
                matched = True
                break
        if not matched:
            false_alarms += 1
    average_lead_time = sum(lead_times) / len(lead_times) if lead_times else None
    return LeadTimeResult(
        warning_count=warning_count,
        false_alarm_count=false_alarms,
        matched_jump_count=len(matched_jump_indices),
        average_lead_time_days=average_lead_time,
    )
