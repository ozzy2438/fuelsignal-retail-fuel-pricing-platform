"""Price-jump label definition and threshold sensitivity analysis.

A "jump" is a day-over-day increase in the fuel_type-level market median retail price
(statewide, across all matched stations) of at least a configurable threshold in cents
per litre. This module is pure Python so the definition is unit-testable independent of
Spark/Databricks; the live Gold pipeline (scripts/run_gold_pipeline.py) computes the same
logic in SQL over the real market-median series and cross-checks against this module's
sensitivity summary.

See docs/jump-label-definition.md for the full empirical sensitivity table and the
reasoning behind the chosen default threshold.

Leakage rule: `jump_today` uses only the current and prior day (no lookahead).
`jump_within_24h`/`jump_within_48h` are TARGET labels and are allowed to use future
days by design - they must never be joined into a feature table.
"""

from __future__ import annotations

from dataclasses import dataclass


def is_jump(daily_change_cpl: float | None, threshold_cpl: float) -> bool:
    """A jump is a day-over-day market median increase >= threshold.

    Always False when `daily_change_cpl` is None (the first day of a series has no
    prior value to compare against, so it can never be a jump day).
    """
    if daily_change_cpl is None:
        return False
    return daily_change_cpl >= threshold_cpl


@dataclass(frozen=True)
class ThresholdSensitivity:
    """Summary statistics for one candidate threshold over one market-day series."""

    threshold_cpl: float
    total_days: int
    event_count: int
    event_frequency: float
    median_magnitude_cpl: float | None
    min_days_between_events: int | None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def summarize_threshold_sensitivity(
    changes: list[float | None], threshold_cpl: float
) -> ThresholdSensitivity:
    """Compute jump-event count/frequency/magnitude/spacing for one candidate threshold.

    Args:
        changes: Ordered day-over-day market median changes (cpl). The first element
            of a series should be None (no prior day to compare against).
        threshold_cpl: Candidate jump threshold in cents per litre.
    """
    total_days = len(changes)
    event_indices = [i for i, change in enumerate(changes) if is_jump(change, threshold_cpl)]
    event_count = len(event_indices)
    magnitudes = [changes[i] for i in event_indices if changes[i] is not None]
    median_magnitude = _median(magnitudes) if magnitudes else None
    gaps = [b - a for a, b in zip(event_indices, event_indices[1:], strict=False)]
    min_gap = min(gaps) if gaps else None
    return ThresholdSensitivity(
        threshold_cpl=threshold_cpl,
        total_days=total_days,
        event_count=event_count,
        event_frequency=(event_count / total_days) if total_days else 0.0,
        median_magnitude_cpl=median_magnitude,
        min_days_between_events=min_gap,
    )


def build_labels(changes: list[float | None], threshold_cpl: float) -> list[dict[str, bool]]:
    """Build jump_today/jump_within_24h/jump_within_48h for an ordered day series.

    `jump_within_24h`/`jump_within_48h` intentionally look at FUTURE days relative to
    each row - they are supervised-learning targets, not features, and must be kept in
    a separate table/columns from anything used as a model input.
    """
    jump_today = [is_jump(change, threshold_cpl) for change in changes]
    n = len(jump_today)
    labels = []
    for i in range(n):
        within_24h = jump_today[i + 1] if i + 1 < n else False
        within_48h = any(jump_today[i + 1 : i + 3])
        labels.append(
            {
                "jump_today": jump_today[i],
                "jump_within_24h": within_24h,
                "jump_within_48h": within_48h,
            }
        )
    return labels
