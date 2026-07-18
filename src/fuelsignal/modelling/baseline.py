"""Transparent rule-based 48h price-jump baseline.

Predicts a market-wide jump within 48h once at least the fuel type's own empirical
median inter-jump gap has passed since the last detected market jump - the "if it's
been about a cycle length since the last jump, expect another one" heuristic from the
original project framing. Deliberately simple and fully explainable: one number
(the cycle length) and one comparison, no learned parameters.
"""

from __future__ import annotations


def days_since_last_jump_series(jump_today: list[bool]) -> list[int | None]:
    """Running trailing-only days-since-last-True.

    `None` for every position before the first True is seen (there is no prior jump
    to measure from yet) - never guessed as 0 or any other placeholder.
    """
    result: list[int | None] = []
    last_jump_index: int | None = None
    for index, flag in enumerate(jump_today):
        if flag:
            last_jump_index = index
        result.append(None if last_jump_index is None else index - last_jump_index)
    return result


def baseline_predict(days_since_last_jump: int | None, cycle_length_days: float) -> bool:
    """Predict jump_within_48h = True once days_since_last_jump reaches the cycle
    length. False whenever there is no prior jump to measure from - a cautious
    default, never a guess.
    """
    if days_since_last_jump is None:
        return False
    return days_since_last_jump >= cycle_length_days


def median_inter_jump_gap_days(jump_today: list[bool]) -> float | None:
    """Median number of days between consecutive True events in an ordered series.

    Returns None when there are fewer than two jump events to measure a gap between.
    """
    jump_indices = [index for index, flag in enumerate(jump_today) if flag]
    if len(jump_indices) < 2:
        return None
    gaps = [b - a for a, b in zip(jump_indices, jump_indices[1:], strict=False)]
    ordered = sorted(gaps)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0
