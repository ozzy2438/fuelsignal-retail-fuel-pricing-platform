"""Pure helpers for evaluating a pricing-policy backtest (Week 2 Phase 4).

Kept separate from pricing_policy.py's decision rule: these functions describe the
*actual observed* historical series (has the price gone stale, was the station
materially overpriced that day), not anything the policy itself decides.
"""

from __future__ import annotations


def days_since_price_change_series(prices: list[float]) -> list[int]:
    """Trailing-only run length of consecutive unchanged prices, ending at each day.

    The first day of a series is always 0 (no prior day to compare against). A day
    whose price differs from the previous day is also 0 (the price just changed).
    Otherwise the count is one more than the previous day's.
    """
    result: list[int] = []
    run_length = 0
    for index, price in enumerate(prices):
        if index == 0 or price != prices[index - 1]:
            run_length = 0
        else:
            run_length += 1
        result.append(run_length)
    return result


def is_stale(days_unchanged: int, stale_days_threshold: int) -> bool:
    """A day is stale once the price has gone unchanged for at least the threshold."""
    return days_unchanged >= stale_days_threshold


def is_priced_above_competitors(
    station_vs_competitor_median_cpl: float | None, follow_min_overpriced_cpl: float
) -> bool:
    """Same "materially overpriced" test the policy itself uses, exposed separately
    so the backtest can measure it against the *actual* historical series regardless
    of what the policy recommended that day."""
    return (
        station_vs_competitor_median_cpl is not None
        and station_vs_competitor_median_cpl >= follow_min_overpriced_cpl
    )
