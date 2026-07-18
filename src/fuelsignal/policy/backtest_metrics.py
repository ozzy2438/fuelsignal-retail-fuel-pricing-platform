"""Pure helpers for evaluating a pricing-policy backtest (Week 2 Phase 4).

Kept separate from pricing_policy.py's decision rule: these functions describe the
*actual observed* historical series (has the price gone stale, was the station
materially overpriced that day), not anything the policy itself decides. Depends
only on pandas (a base dependency), never on mlflow/lightgbm/Databricks - so this
module (and its tests) import cleanly in CI, unlike the orchestration script that
calls it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


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


def summarize(recommendations: pd.DataFrame, fuel_type: str | None) -> dict[str, Any]:
    """Aggregate one fuel type's (or, with `fuel_type=None`, every fuel type's)
    backtest rows: HOLD/FOLLOW/LEAD counts, guardrail interventions, staleness and
    above-competitor exposure vs. the always-HOLD baseline, indicative margin impact,
    signal contribution, and the LEAD hit rate against actual outcomes."""
    frame = (
        recommendations
        if fuel_type is None
        else recommendations[recommendations["fuel_type"] == fuel_type]
    )
    row_count = len(frame)
    lead_rows = frame[frame["action"] == "LEAD"]
    has_margin_data = bool(frame["margin_difference_cpl"].notna().any())
    return {
        "fuel_type": fuel_type or "ALL",
        "policy_mode": (frame["policy_mode"].iloc[0] if row_count and fuel_type else "mixed"),
        "hold_count": int((frame["action"] == "HOLD").sum()),
        "follow_count": int((frame["action"] == "FOLLOW").sum()),
        "lead_count": int((frame["action"] == "LEAD").sum()),
        "baseline_hold_count": row_count,
        "guardrail_intervention_count": int(frame["guardrail_triggered"].sum()),
        "stale_price_days_policy": int(
            (frame["is_stale_actual"] & (frame["action"] == "HOLD")).sum()
        ),
        "stale_price_days_baseline": int(frame["is_stale_actual"].sum()),
        "days_priced_above_competitors_actual": int(frame["priced_above_competitors_actual"].sum()),
        "days_priced_above_competitors_unaddressed": int(
            (frame["priced_above_competitors_actual"] & (frame["action"] != "FOLLOW")).sum()
        ),
        # Three-way safety gate (Phase 5): "automated" only when both jump-model
        # eligibility and a validated TGP margin guardrail are present;
        # "disabled_unsafe" for any FOLLOW with no margin data to guard it (never
        # silently converted to HOLD - the raw action stays visible in `action`).
        "automated_status_count": int((frame["recommendation_status"] == "automated").sum()),
        "watch_only_status_count": int((frame["recommendation_status"] == "watch_only").sum()),
        "disabled_unsafe_status_count": int(
            (frame["recommendation_status"] == "disabled_unsafe").sum()
        ),
        # tgp_cpl (and therefore any margin figure) is only populated for DL/U91 -
        # established in Phase 1, confirmed again live for this window (2026-07-18):
        # E10/P95/P98/PDL have 0 non-null tgp_cpl rows. Report None, not a misleading
        # 0.0, when no margin-bearing row exists to average - a real "no data" must
        # never look like a real "no impact".
        "margin_data_available": has_margin_data,
        "avg_margin_difference_cpl": (
            float(frame["margin_difference_cpl"].dropna().mean()) if has_margin_data else None
        ),
        "total_margin_difference_cpl": (
            float(frame["margin_difference_cpl"].dropna().sum()) if has_margin_data else None
        ),
        "jump_signal_contribution_count": int(frame["jump_signal_used"].sum()),
        "forecast_signal_contribution_count": int(frame["forecast_signal_used"].sum()),
        "lead_hit_rate": (float((lead_rows["jump_within_48h"]).mean()) if len(lead_rows) else None),
        "row_count": row_count,
    }
