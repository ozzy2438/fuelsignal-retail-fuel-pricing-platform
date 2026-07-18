"""Tests for the pricing-policy backtest orchestration script."""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_pricing_policy_backtest.py"
SCRIPTS_DIR = str(SCRIPT_PATH.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
SPEC = importlib.util.spec_from_file_location("run_pricing_policy_backtest", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
policy_backtest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy_backtest
SPEC.loader.exec_module(policy_backtest)


def _frame(margin_diffs: list[float | None]) -> pd.DataFrame:
    n = len(margin_diffs)
    return pd.DataFrame(
        {
            "fuel_type": ["E10"] * n,
            "policy_mode": ["automated"] * n,
            "action": ["FOLLOW"] * n,
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
    summary = policy_backtest.summarize(frame, "E10")
    assert summary["margin_data_available"] is False
    assert summary["avg_margin_difference_cpl"] is None
    assert summary["total_margin_difference_cpl"] is None


@pytest.mark.unit
def test_summarize_computes_real_averages_when_margin_data_exists() -> None:
    frame = _frame([-10.0, -20.0, None])
    summary = policy_backtest.summarize(frame, "E10")
    assert summary["margin_data_available"] is True
    assert summary["avg_margin_difference_cpl"] == pytest.approx(-15.0)
    assert summary["total_margin_difference_cpl"] == pytest.approx(-30.0)
