"""Tests for the price-jump label definition and sensitivity analysis."""

import pytest

from fuelsignal.gold.jump_labels import (
    build_labels,
    is_jump,
    summarize_threshold_sensitivity,
)


@pytest.mark.unit
def test_is_jump_true_at_and_above_threshold() -> None:
    assert is_jump(7.0, threshold_cpl=7.0)
    assert is_jump(10.0, threshold_cpl=7.0)


@pytest.mark.unit
def test_is_jump_false_below_threshold() -> None:
    assert not is_jump(6.99, threshold_cpl=7.0)
    assert not is_jump(-5.0, threshold_cpl=7.0)


@pytest.mark.unit
def test_is_jump_false_when_no_prior_day() -> None:
    assert not is_jump(None, threshold_cpl=7.0)


@pytest.mark.unit
def test_summarize_threshold_sensitivity_counts_and_frequency() -> None:
    changes = [None, 1.0, 8.0, 2.0, 9.0, 0.0]
    result = summarize_threshold_sensitivity(changes, threshold_cpl=7.0)
    assert result.total_days == 6
    assert result.event_count == 2
    assert result.event_frequency == pytest.approx(2 / 6)
    assert result.median_magnitude_cpl == pytest.approx(8.5)


@pytest.mark.unit
def test_summarize_threshold_sensitivity_min_gap_between_events() -> None:
    changes = [None, 8.0, 1.0, 8.0, 1.0, 1.0, 8.0]
    result = summarize_threshold_sensitivity(changes, threshold_cpl=7.0)
    # Events at indices 1, 3, 6 -> gaps of 2 and 3
    assert result.min_days_between_events == 2


@pytest.mark.unit
def test_summarize_threshold_sensitivity_no_events() -> None:
    changes = [None, 1.0, 2.0, 3.0]
    result = summarize_threshold_sensitivity(changes, threshold_cpl=7.0)
    assert result.event_count == 0
    assert result.median_magnitude_cpl is None
    assert result.min_days_between_events is None


@pytest.mark.unit
def test_summarize_threshold_sensitivity_empty_series() -> None:
    result = summarize_threshold_sensitivity([], threshold_cpl=7.0)
    assert result.total_days == 0
    assert result.event_frequency == 0.0


@pytest.mark.unit
def test_build_labels_jump_today_matches_is_jump() -> None:
    changes = [None, 8.0, 1.0]
    labels = build_labels(changes, threshold_cpl=7.0)
    assert [entry["jump_today"] for entry in labels] == [False, True, False]


@pytest.mark.unit
def test_build_labels_within_24h_looks_one_day_forward_only() -> None:
    changes = [1.0, 1.0, 8.0, 1.0]
    labels = build_labels(changes, threshold_cpl=7.0)
    # index 1's next day (index 2) is a jump -> within_24h True for index 1
    assert labels[1]["jump_within_24h"] is True
    # index 0's next day (index 1) is not a jump -> within_24h False for index 0
    assert labels[0]["jump_within_24h"] is False


@pytest.mark.unit
def test_build_labels_within_48h_looks_two_days_forward() -> None:
    changes = [1.0, 1.0, 1.0, 8.0]
    labels = build_labels(changes, threshold_cpl=7.0)
    # index 1: next two days are indices 2,3 -> index 3 is a jump -> True
    assert labels[1]["jump_within_48h"] is True
    # index 0: next two days are indices 1,2 -> neither is a jump -> False
    assert labels[0]["jump_within_48h"] is False


@pytest.mark.unit
def test_build_labels_end_of_series_does_not_look_past_the_end() -> None:
    changes = [1.0, 8.0]
    labels = build_labels(changes, threshold_cpl=7.0)
    # last element has no future days at all
    assert labels[-1]["jump_within_24h"] is False
    assert labels[-1]["jump_within_48h"] is False


@pytest.mark.unit
def test_build_labels_never_marks_jump_today_from_future() -> None:
    # A large future jump must not affect jump_today for an earlier, non-jump day.
    changes = [1.0, 1.0, 20.0]
    labels = build_labels(changes, threshold_cpl=7.0)
    assert labels[0]["jump_today"] is False
    assert labels[1]["jump_today"] is False
