"""Tests for the pricing-policy backtest aggregate summary (regression coverage for
the margin-null-vs-zero reporting fix)."""

import pandas as pd
import pytest

from fuelsignal.policy.backtest_metrics import summarize


def _frame(margin_diffs: list[float | None], statuses: list[str] | None = None) -> pd.DataFrame:
    n = len(margin_diffs)
    return pd.DataFrame(
        {
            "fuel_type": ["E10"] * n,
            "policy_mode": ["automated"] * n,
            "action": ["FOLLOW"] * n,
            "recommendation_status": statuses or (["disabled_unsafe"] * n),
            "guardrail_triggered": [False] * n,
            "is_stale_actual": [False] * n,
            "priced_above_competitors_actual": [True] * n,
            "margin_difference_cpl": margin_diffs,
            "jump_signal_used": [False] * n,
            "forecast_signal_used": [True] * n,
            "jump_within_48h": [False] * n,
        }
    )


@pytest.mark.unit
def test_summarize_reports_none_not_zero_when_no_margin_data() -> None:
    # Reproduces the live TGP-unavailable case (E10/P95/P98/PDL): every row has a
    # null margin_difference_cpl because tgp_cpl was never available to compute one.
    frame = _frame([None, None, None])
    summary = summarize(frame, "E10")
    assert summary["margin_data_available"] is False
    assert summary["avg_margin_difference_cpl"] is None
    assert summary["total_margin_difference_cpl"] is None


@pytest.mark.unit
def test_summarize_computes_real_averages_when_margin_data_exists() -> None:
    frame = _frame([-10.0, -20.0, None], statuses=["automated", "automated", "automated"])
    summary = summarize(frame, "E10")
    assert summary["margin_data_available"] is True
    assert summary["avg_margin_difference_cpl"] == pytest.approx(-15.0)
    assert summary["total_margin_difference_cpl"] == pytest.approx(-30.0)


@pytest.mark.unit
def test_summarize_counts_recommendation_status_breakdown() -> None:
    frame = _frame(
        [None, None, -5.0, -5.0],
        statuses=["disabled_unsafe", "disabled_unsafe", "automated", "watch_only"],
    )
    summary = summarize(frame, "E10")
    assert summary["disabled_unsafe_status_count"] == 2
    assert summary["automated_status_count"] == 1
    assert summary["watch_only_status_count"] == 1
