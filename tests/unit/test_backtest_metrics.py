"""Tests for pricing-policy backtest evaluation helpers."""

import pytest

from fuelsignal.policy.backtest_metrics import (
    days_since_price_change_series,
    is_priced_above_competitors,
    is_stale,
)


@pytest.mark.unit
def test_days_since_price_change_first_day_is_zero() -> None:
    assert days_since_price_change_series([180.0])[0] == 0


@pytest.mark.unit
def test_days_since_price_change_increments_while_unchanged() -> None:
    prices = [180.0, 180.0, 180.0, 180.0]
    assert days_since_price_change_series(prices) == [0, 1, 2, 3]


@pytest.mark.unit
def test_days_since_price_change_resets_on_change() -> None:
    prices = [180.0, 180.0, 182.0, 182.0, 182.0, 179.0]
    assert days_since_price_change_series(prices) == [0, 1, 0, 1, 2, 0]


@pytest.mark.unit
def test_days_since_price_change_empty_series() -> None:
    assert days_since_price_change_series([]) == []


@pytest.mark.unit
def test_is_stale_threshold_boundary() -> None:
    assert is_stale(days_unchanged=7, stale_days_threshold=7)
    assert not is_stale(days_unchanged=6, stale_days_threshold=7)


@pytest.mark.unit
def test_is_priced_above_competitors_true_and_false() -> None:
    assert is_priced_above_competitors(5.0, follow_min_overpriced_cpl=4.0)
    assert not is_priced_above_competitors(3.9, follow_min_overpriced_cpl=4.0)


@pytest.mark.unit
def test_is_priced_above_competitors_none_is_false() -> None:
    assert not is_priced_above_competitors(None, follow_min_overpriced_cpl=4.0)
