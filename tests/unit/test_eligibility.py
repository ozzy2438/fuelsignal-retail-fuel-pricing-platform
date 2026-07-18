"""Tests for the model-eligibility decision logic."""

import pytest

from fuelsignal.modelling.eligibility import evaluate_eligibility


@pytest.mark.unit
def test_eligible_series_passes_both_criteria() -> None:
    result = evaluate_eligibility(
        total_observations=100,
        extreme_change_rate=0.02,
        min_observations=30,
        max_extreme_change_rate=0.10,
    )
    assert result.is_eligible
    assert result.exclusion_reason is None


@pytest.mark.unit
def test_excluded_for_insufficient_observations() -> None:
    result = evaluate_eligibility(
        total_observations=10,
        extreme_change_rate=0.0,
        min_observations=30,
        max_extreme_change_rate=0.10,
    )
    assert not result.is_eligible
    assert result.exclusion_reason == "insufficient_observations"


@pytest.mark.unit
def test_excluded_for_extreme_volatility() -> None:
    result = evaluate_eligibility(
        total_observations=100,
        extreme_change_rate=0.25,
        min_observations=30,
        max_extreme_change_rate=0.10,
    )
    assert not result.is_eligible
    assert result.exclusion_reason == "extreme_price_volatility"


@pytest.mark.unit
def test_excluded_for_both_reasons_reports_both() -> None:
    result = evaluate_eligibility(
        total_observations=5,
        extreme_change_rate=0.5,
        min_observations=30,
        max_extreme_change_rate=0.10,
    )
    assert not result.is_eligible
    assert result.exclusion_reason == "insufficient_observations+extreme_price_volatility"


@pytest.mark.unit
def test_boundary_values_are_inclusive_of_pass() -> None:
    # exactly at the minimum observation count and exactly at the max rate: both pass
    result = evaluate_eligibility(
        total_observations=30,
        extreme_change_rate=0.10,
        min_observations=30,
        max_extreme_change_rate=0.10,
    )
    assert result.is_eligible
