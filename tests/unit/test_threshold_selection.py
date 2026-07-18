"""Tests for the business-oriented threshold selection rule."""

import pytest

from fuelsignal.modelling.threshold_selection import ThresholdCandidate, select_threshold


def _candidate(threshold, precision, recall, f1, fpr=0.1, alerts=10, fapm=5.0, lead=1.5):
    return ThresholdCandidate(
        threshold=threshold,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=fpr,
        alerts=alerts,
        false_alarms_per_market_month=fapm,
        average_lead_time_days=lead,
    )


@pytest.mark.unit
def test_selects_highest_f1_among_qualifying_candidates() -> None:
    candidates = [
        _candidate(0.3, precision=0.2, recall=0.6, f1=0.30, fapm=5.0, lead=1.5),
        _candidate(0.5, precision=0.4, recall=0.4, f1=0.40, fapm=3.0, lead=1.5),
        _candidate(0.7, precision=0.6, recall=0.1, f1=0.17, fapm=1.0, lead=1.5),
    ]
    result = select_threshold(
        candidates,
        min_recall=0.2,
        max_false_alarms_per_market_month=8.0,
        min_avg_lead_time_days=1.0,
    )
    assert result.chosen_threshold == 0.5
    assert result.reason == "highest_f1_among_qualifying"


@pytest.mark.unit
def test_does_not_pick_max_f1_when_it_fails_recall_floor() -> None:
    # threshold 0.7 has the highest F1 but fails the recall floor
    candidates = [
        _candidate(0.3, precision=0.2, recall=0.6, f1=0.30),
        _candidate(0.7, precision=0.9, recall=0.05, f1=0.09),
    ]
    result = select_threshold(
        candidates,
        min_recall=0.2,
        max_false_alarms_per_market_month=8.0,
        min_avg_lead_time_days=1.0,
    )
    assert result.chosen_threshold == 0.3


@pytest.mark.unit
def test_rejects_candidate_exceeding_alert_fatigue_cap() -> None:
    candidates = [
        _candidate(0.2, precision=0.1, recall=0.9, f1=0.18, fapm=20.0),  # too many alerts
    ]
    result = select_threshold(
        candidates,
        min_recall=0.2,
        max_false_alarms_per_market_month=8.0,
        min_avg_lead_time_days=1.0,
    )
    assert result.chosen_threshold is None
    assert result.reason == "no_threshold_satisfies_constraints"


@pytest.mark.unit
def test_rejects_candidate_with_insufficient_lead_time() -> None:
    candidates = [
        _candidate(0.4, precision=0.3, recall=0.5, f1=0.375, lead=0.3),
    ]
    result = select_threshold(
        candidates,
        min_recall=0.2,
        max_false_alarms_per_market_month=8.0,
        min_avg_lead_time_days=1.0,
    )
    assert result.chosen_threshold is None


@pytest.mark.unit
def test_rejects_candidate_with_no_lead_time_measured() -> None:
    candidates = [
        _candidate(0.9, precision=0.0, recall=0.0, f1=0.0, alerts=0, fapm=0.0, lead=None),
    ]
    result = select_threshold(
        candidates,
        min_recall=0.0,
        max_false_alarms_per_market_month=8.0,
        min_avg_lead_time_days=1.0,
    )
    assert result.chosen_threshold is None


@pytest.mark.unit
def test_returns_none_when_no_candidates_given() -> None:
    result = select_threshold(
        [], min_recall=0.2, max_false_alarms_per_market_month=8.0, min_avg_lead_time_days=1.0
    )
    assert result.chosen_threshold is None
    assert result.candidate is None
