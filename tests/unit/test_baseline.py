"""Tests for the transparent rule-based price-jump baseline."""

import pytest

from fuelsignal.modelling.baseline import (
    baseline_predict,
    days_since_last_jump_series,
    median_inter_jump_gap_days,
)


@pytest.mark.unit
def test_days_since_last_jump_series_is_none_before_first_jump() -> None:
    result = days_since_last_jump_series([False, False, False])
    assert result == [None, None, None]


@pytest.mark.unit
def test_days_since_last_jump_series_counts_from_jump_day() -> None:
    result = days_since_last_jump_series([False, False, True, False, False, False, True])
    assert result == [None, None, 0, 1, 2, 3, 0]


@pytest.mark.unit
def test_baseline_predict_false_with_no_prior_jump() -> None:
    assert baseline_predict(None, cycle_length_days=7.0) is False


@pytest.mark.unit
def test_baseline_predict_true_once_cycle_length_reached() -> None:
    assert baseline_predict(7, cycle_length_days=7.0) is True
    assert baseline_predict(6, cycle_length_days=7.0) is False
    assert baseline_predict(8, cycle_length_days=7.0) is True


@pytest.mark.unit
def test_median_inter_jump_gap_requires_at_least_two_events() -> None:
    assert median_inter_jump_gap_days([False, True, False, False]) is None
    assert median_inter_jump_gap_days([False, False, False]) is None


@pytest.mark.unit
def test_median_inter_jump_gap_computes_correctly() -> None:
    # jumps at indices 1, 8, 12 -> gaps of 7 and 4 -> median 5.5
    series = [False] * 13
    series[1] = True
    series[8] = True
    series[12] = True
    assert median_inter_jump_gap_days(series) == pytest.approx(5.5)


@pytest.mark.unit
def test_median_inter_jump_gap_odd_number_of_gaps() -> None:
    # jumps at 0, 3, 9 -> gaps 3, 6 -> even count -> average 4.5
    series = [False] * 10
    series[0] = True
    series[3] = True
    series[9] = True
    assert median_inter_jump_gap_days(series) == pytest.approx(4.5)
