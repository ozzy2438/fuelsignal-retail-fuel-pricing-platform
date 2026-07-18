"""Tests for the non-ML seven-day price forecast baselines."""

import pytest

from fuelsignal.modelling.forecast_baselines import (
    linear_trend_forecast,
    moving_average_forecast,
    persistence_forecast,
)


@pytest.mark.unit
def test_persistence_forecast_returns_last_price() -> None:
    assert persistence_forecast([180.0, 182.0, 179.5]) == 179.5


@pytest.mark.unit
def test_persistence_forecast_empty_returns_none() -> None:
    assert persistence_forecast([]) is None


@pytest.mark.unit
def test_moving_average_forecast_uses_trailing_window_only() -> None:
    prices = [100.0, 200.0, 10.0, 20.0, 30.0]
    # window=3 must ignore the first two points (100.0, 200.0)
    assert moving_average_forecast(prices, window=3) == pytest.approx(20.0)


@pytest.mark.unit
def test_moving_average_forecast_shorter_history_than_window() -> None:
    assert moving_average_forecast([10.0, 20.0], window=7) == pytest.approx(15.0)


@pytest.mark.unit
def test_moving_average_forecast_empty_returns_none() -> None:
    assert moving_average_forecast([], window=7) is None


@pytest.mark.unit
def test_linear_trend_forecast_extrapolates_a_straight_line() -> None:
    # perfectly linear: price increases by 2.0 cpl/day
    prices = [100.0, 102.0, 104.0, 106.0, 108.0]
    # last point is x=4 (price 108.0); horizon_days=3 -> x=7 -> 100 + 2*7 = 114.0
    assert linear_trend_forecast(prices, window=5, horizon_days=3) == pytest.approx(114.0)


@pytest.mark.unit
def test_linear_trend_forecast_flat_series_returns_flat_value() -> None:
    prices = [150.0, 150.0, 150.0]
    assert linear_trend_forecast(prices, window=3, horizon_days=7) == pytest.approx(150.0)


@pytest.mark.unit
def test_linear_trend_forecast_single_point_returns_none() -> None:
    assert linear_trend_forecast([150.0], window=14, horizon_days=1) is None


@pytest.mark.unit
def test_linear_trend_forecast_empty_returns_none() -> None:
    assert linear_trend_forecast([], window=14, horizon_days=1) is None
