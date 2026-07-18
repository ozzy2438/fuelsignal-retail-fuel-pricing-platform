"""Tests for the HOLD / FOLLOW / LEAD pricing-policy decision rule."""

import pytest

from fuelsignal.policy.pricing_policy import PolicyInputs, PolicyParams, decide_policy

PARAMS = PolicyParams(
    lead_min_forecast_change_cpl=5.0,
    lead_step_cpl=2.0,
    follow_min_overpriced_cpl=4.0,
    follow_forecast_decline_cpl=5.0,
    min_margin_guardrail_cpl=1.0,
)


def _inputs(**overrides) -> PolicyInputs:
    defaults = {
        "fuel_type": "E10",
        "automation_enabled": True,
        "current_price_cpl": 180.0,
        "jump_probability": 0.1,
        "jump_threshold": 0.4,
        "forecast_3d_change_cpl": 0.0,
        "station_vs_competitor_median_cpl": 0.0,
        "tgp_cpl": 165.0,
        "indicative_margin_cpl": 15.0,
    }
    defaults.update(overrides)
    return PolicyInputs(**defaults)


@pytest.mark.unit
def test_hold_when_no_trigger_fires() -> None:
    decision = decide_policy(_inputs(), PARAMS)
    assert decision.action == "HOLD"
    assert decision.reason == "no_trigger"
    assert not decision.guardrail_triggered


@pytest.mark.unit
def test_hold_when_current_price_missing() -> None:
    decision = decide_policy(_inputs(current_price_cpl=None), PARAMS)
    assert decision.action == "HOLD"
    assert decision.reason == "insufficient_data"


@pytest.mark.unit
def test_lead_when_jump_expected_and_forecast_rising_and_not_overpriced() -> None:
    decision = decide_policy(
        _inputs(jump_probability=0.6, jump_threshold=0.4, forecast_3d_change_cpl=6.0),
        PARAMS,
    )
    assert decision.action == "LEAD"
    assert decision.reason == "jump_signal_and_rising_forecast"
    assert decision.hypothetical_price_cpl == pytest.approx(182.0)
    assert decision.hypothetical_margin_cpl == pytest.approx(17.0)
    assert decision.mode == "automated"
    assert decision.recommendation_status == "automated"


@pytest.mark.unit
def test_lead_is_automated_even_without_margin_data() -> None:
    # Raising price cannot breach a margin floor - LEAD never needs TGP to be safe.
    decision = decide_policy(
        _inputs(
            jump_probability=0.6,
            jump_threshold=0.4,
            forecast_3d_change_cpl=6.0,
            tgp_cpl=None,
        ),
        PARAMS,
    )
    assert decision.action == "LEAD"
    assert decision.recommendation_status == "automated"


@pytest.mark.unit
def test_no_lead_when_automation_disabled_even_if_signals_agree() -> None:
    # U91/P95: jump probability alone must never trigger LEAD when automation is off.
    decision = decide_policy(
        _inputs(
            fuel_type="U91",
            automation_enabled=False,
            jump_probability=0.9,
            jump_threshold=0.5,
            forecast_3d_change_cpl=6.0,
        ),
        PARAMS,
    )
    assert decision.action != "LEAD"
    assert decision.mode == "watch_only"


@pytest.mark.unit
def test_no_lead_when_already_priced_above_market_falls_to_follow() -> None:
    decision = decide_policy(
        _inputs(
            jump_probability=0.6,
            jump_threshold=0.4,
            forecast_3d_change_cpl=6.0,
            station_vs_competitor_median_cpl=5.0,
        ),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.reason == "priced_above_competitor_median"


@pytest.mark.unit
def test_follow_when_priced_above_competitor_median() -> None:
    decision = decide_policy(
        _inputs(station_vs_competitor_median_cpl=6.0),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.hypothetical_price_cpl == pytest.approx(174.0)  # 180 - 6
    assert not decision.guardrail_triggered
    assert decision.recommendation_status == "automated"  # automated + tgp present (DL-like)


@pytest.mark.unit
def test_follow_is_disabled_unsafe_without_margin_data_even_when_automated() -> None:
    # E10/P98/PDL: jump-model automation is on, but TGP is unavailable - item 3/5
    # requires FOLLOW to never be exposed as automated in that case.
    decision = decide_policy(
        _inputs(
            fuel_type="E10",
            automation_enabled=True,
            station_vs_competitor_median_cpl=6.0,
            tgp_cpl=None,
        ),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.recommendation_status == "disabled_unsafe"
    assert decision.hypothetical_margin_cpl is None


@pytest.mark.unit
def test_follow_is_disabled_unsafe_for_watch_only_fuel_without_margin_data() -> None:
    # P95: neither jump-eligible nor TGP-covered - the strictest case.
    decision = decide_policy(
        _inputs(
            fuel_type="P95",
            automation_enabled=False,
            station_vs_competitor_median_cpl=6.0,
            tgp_cpl=None,
        ),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.recommendation_status == "disabled_unsafe"


@pytest.mark.unit
def test_follow_when_forecast_declines_even_without_current_overpricing() -> None:
    decision = decide_policy(
        _inputs(forecast_3d_change_cpl=-6.0),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.reason == "forecast_decline"
    assert decision.hypothetical_price_cpl == pytest.approx(174.0)  # 180 - 6


@pytest.mark.unit
def test_follow_capped_by_margin_guardrail() -> None:
    # station priced 20 cpl above competitor median -> target 160.0, but tgp=165.0
    # means target margin would be -5.0, well under the 1.0 floor.
    decision = decide_policy(
        _inputs(current_price_cpl=180.0, station_vs_competitor_median_cpl=20.0, tgp_cpl=165.0),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.guardrail_triggered
    assert decision.reason == "priced_above_competitor_median_margin_capped"
    assert decision.hypothetical_price_cpl == pytest.approx(166.0)  # tgp + floor
    assert decision.recommendation_status == "automated"  # tgp present, automation on


@pytest.mark.unit
def test_guardrail_fully_blocks_follow_when_capped_price_not_below_current() -> None:
    # capped price (tgp + floor = 166.0) is already >= current price -> nothing to cut.
    decision = decide_policy(
        _inputs(
            current_price_cpl=165.5,
            station_vs_competitor_median_cpl=20.0,
            tgp_cpl=165.0,
        ),
        PARAMS,
    )
    assert decision.action == "HOLD"
    assert decision.reason == "margin_guardrail_blocked_follow"
    assert decision.guardrail_triggered


@pytest.mark.unit
def test_watch_only_fuel_type_can_still_follow() -> None:
    # tgp_cpl left at the fixture default (165.0, margin data present) - this
    # represents U91 in the live system: no jump automation, but TGP is available so
    # the FOLLOW recommendation is watch_only (advisory), not disabled_unsafe.
    decision = decide_policy(
        _inputs(fuel_type="U91", automation_enabled=False, station_vs_competitor_median_cpl=6.0),
        PARAMS,
    )
    assert decision.action == "FOLLOW"
    assert decision.mode == "watch_only"
    assert decision.recommendation_status == "watch_only"
