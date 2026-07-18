"""Tests for seven-day price forecast metrics and market-phase classification."""

import pytest

from fuelsignal.modelling.forecast_metrics import (
    classify_market_phase,
    directional_accuracy,
    mape,
    wape,
)


@pytest.mark.unit
def test_classify_market_phase_jump_wins_over_negative_change() -> None:
    assert classify_market_phase(jump_today=True, market_daily_change_cpl=-2.0) == "jump"


@pytest.mark.unit
def test_classify_market_phase_decline() -> None:
    assert classify_market_phase(jump_today=False, market_daily_change_cpl=-3.5) == "decline"


@pytest.mark.unit
def test_classify_market_phase_other_for_flat_or_positive() -> None:
    assert classify_market_phase(jump_today=False, market_daily_change_cpl=0.0) == "other"
    assert classify_market_phase(jump_today=False, market_daily_change_cpl=1.5) == "other"


@pytest.mark.unit
def test_classify_market_phase_other_when_change_unknown() -> None:
    assert classify_market_phase(jump_today=False, market_daily_change_cpl=None) == "other"


@pytest.mark.unit
def test_mape_basic() -> None:
    actual = [100.0, 200.0]
    predicted = [110.0, 180.0]
    # errors: |100-110|/100=0.10, |200-180|/200=0.10 -> mean 0.10 -> 10.0%
    assert mape(actual, predicted) == pytest.approx(10.0)


@pytest.mark.unit
def test_mape_skips_zero_actual() -> None:
    actual = [0.0, 100.0]
    predicted = [50.0, 90.0]
    # only the second pair counts: |100-90|/100 = 0.10 -> 10.0%
    assert mape(actual, predicted) == pytest.approx(10.0)


@pytest.mark.unit
def test_mape_all_zero_actual_returns_none() -> None:
    assert mape([0.0, 0.0], [1.0, 2.0]) is None


@pytest.mark.unit
def test_wape_basic() -> None:
    actual = [100.0, 200.0]
    predicted = [110.0, 190.0]
    # numerator = 10 + 10 = 20, denominator = 300 -> 6.666...%
    assert wape(actual, predicted) == pytest.approx(20.0 / 300.0 * 100.0)


@pytest.mark.unit
def test_wape_zero_denominator_returns_none() -> None:
    assert wape([0.0, 0.0], [1.0, 2.0]) is None


@pytest.mark.unit
def test_directional_accuracy_all_correct() -> None:
    last_observed = [100.0, 100.0]
    actual = [105.0, 95.0]  # up, down
    predicted = [102.0, 90.0]  # up, down
    assert directional_accuracy(last_observed, actual, predicted) == pytest.approx(1.0)


@pytest.mark.unit
def test_directional_accuracy_partial() -> None:
    last_observed = [100.0, 100.0]
    actual = [105.0, 95.0]  # up, down
    predicted = [102.0, 110.0]  # up, up (wrong on the second)
    assert directional_accuracy(last_observed, actual, predicted) == pytest.approx(0.5)


@pytest.mark.unit
def test_directional_accuracy_flat_prediction_must_match_flat_actual() -> None:
    last_observed = [100.0]
    actual = [100.0]  # genuinely flat
    predicted = [100.0]  # also flat
    assert directional_accuracy(last_observed, actual, predicted) == pytest.approx(1.0)


@pytest.mark.unit
def test_directional_accuracy_flat_prediction_wrong_against_moved_actual() -> None:
    last_observed = [100.0]
    actual = [105.0]  # actually moved up
    predicted = [100.0]  # predicted flat - not a free pass
    assert directional_accuracy(last_observed, actual, predicted) == pytest.approx(0.0)


@pytest.mark.unit
def test_directional_accuracy_empty_returns_none() -> None:
    assert directional_accuracy([], [], []) is None
