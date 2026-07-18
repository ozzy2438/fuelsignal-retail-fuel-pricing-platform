"""Tests for market-day warning aggregation and lead-time/false-alarm evaluation."""

import pytest

from fuelsignal.modelling.evaluation import (
    aggregate_to_market_day,
    evaluate_market_day_warnings,
)


@pytest.mark.unit
def test_aggregate_to_market_day_majority_positive() -> None:
    assert aggregate_to_market_day([True, True, False]) is True


@pytest.mark.unit
def test_aggregate_to_market_day_majority_negative() -> None:
    assert aggregate_to_market_day([True, False, False]) is False


@pytest.mark.unit
def test_aggregate_to_market_day_tie_counts_as_positive() -> None:
    assert aggregate_to_market_day([True, False]) is True


@pytest.mark.unit
def test_aggregate_to_market_day_empty_is_no_warning() -> None:
    assert aggregate_to_market_day([]) is False


@pytest.mark.unit
def test_evaluate_market_day_warnings_matches_jump_within_window() -> None:
    # warning on day 0, actual jump on day 2 -> lead time 2, matched
    predicted = [True, False, False]
    actual = [False, False, True]
    result = evaluate_market_day_warnings(predicted, actual, max_lead_days=2)
    assert result.warning_count == 1
    assert result.false_alarm_count == 0
    assert result.matched_jump_count == 1
    assert result.average_lead_time_days == pytest.approx(2.0)


@pytest.mark.unit
def test_evaluate_market_day_warnings_false_alarm_when_no_jump_follows() -> None:
    predicted = [True, False, False]
    actual = [False, False, False]
    result = evaluate_market_day_warnings(predicted, actual, max_lead_days=2)
    assert result.warning_count == 1
    assert result.false_alarm_count == 1
    assert result.average_lead_time_days is None


@pytest.mark.unit
def test_evaluate_market_day_warnings_jump_outside_window_is_false_alarm() -> None:
    predicted = [True, False, False, False]
    actual = [False, False, False, True]  # jump 3 days later, window is 2
    result = evaluate_market_day_warnings(predicted, actual, max_lead_days=2)
    assert result.false_alarm_count == 1
    assert result.matched_jump_count == 0


@pytest.mark.unit
def test_evaluate_market_day_warnings_picks_earliest_matching_jump() -> None:
    predicted = [True, False, False]
    actual = [False, True, True]
    result = evaluate_market_day_warnings(predicted, actual, max_lead_days=2)
    assert result.average_lead_time_days == pytest.approx(1.0)
    assert result.matched_jump_count == 1


@pytest.mark.unit
def test_evaluate_market_day_warnings_averages_multiple_lead_times() -> None:
    # warning@0 -> jump@1 (lead 1); warning@3 -> jump@5 (lead 2)
    predicted = [True, False, False, True, False, False]
    actual = [False, True, False, False, False, True]
    result = evaluate_market_day_warnings(predicted, actual, max_lead_days=2)
    assert result.warning_count == 2
    assert result.false_alarm_count == 0
    assert result.average_lead_time_days == pytest.approx(1.5)


@pytest.mark.unit
def test_evaluate_market_day_warnings_no_warnings_is_empty_result() -> None:
    result = evaluate_market_day_warnings([False, False], [False, True], max_lead_days=2)
    assert result.warning_count == 0
    assert result.false_alarm_count == 0
    assert result.average_lead_time_days is None
