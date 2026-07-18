"""Tests for walk-forward (expanding-window) validation split logic."""

from datetime import date

import pytest

from fuelsignal.modelling.walk_forward import build_walk_forward_folds


@pytest.mark.unit
def test_first_fold_trains_from_series_start() -> None:
    folds = build_walk_forward_folds(
        start_date=date(2025, 1, 1),
        end_date=date(2026, 6, 30),
        min_train_days=180,
        test_days=60,
        n_folds=4,
    )
    assert folds[0].train_start == date(2025, 1, 1)
    assert folds[0].train_end == date(2025, 6, 29)
    assert folds[0].test_start == date(2025, 6, 30)
    assert folds[0].test_end == date(2025, 8, 28)


@pytest.mark.unit
def test_train_window_expands_and_never_overlaps_its_own_test() -> None:
    folds = build_walk_forward_folds(
        start_date=date(2025, 1, 1),
        end_date=date(2026, 6, 30),
        min_train_days=180,
        test_days=60,
        n_folds=4,
    )
    for fold in folds:
        assert fold.train_start == date(2025, 1, 1)
        assert fold.train_end < fold.test_start
        assert fold.test_start <= fold.test_end

    # each successive fold's train window subsumes the previous fold's test window
    for earlier, later in zip(folds, folds[1:], strict=False):
        assert later.train_end == earlier.test_end
        assert later.test_start > earlier.test_end


@pytest.mark.unit
def test_returns_fewer_folds_when_range_cannot_support_all_requested() -> None:
    folds = build_walk_forward_folds(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 4, 1),  # only ~90 days total
        min_train_days=180,
        test_days=60,
        n_folds=4,
    )
    assert folds == []


@pytest.mark.unit
def test_returns_exactly_the_folds_that_fit() -> None:
    # 180 train + 1*60 test = 240 days fits; a second 60-day test window (300 days
    # total) does not fit inside a ~250-day range.
    folds = build_walk_forward_folds(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 9, 7),  # 250 days after start_date
        min_train_days=180,
        test_days=60,
        n_folds=4,
    )
    assert len(folds) == 1


@pytest.mark.unit
def test_no_fold_test_period_extends_past_end_date() -> None:
    end = date(2026, 6, 30)
    folds = build_walk_forward_folds(
        start_date=date(2025, 1, 1), end_date=end, min_train_days=180, test_days=60, n_folds=10
    )
    for fold in folds:
        assert fold.test_end <= end


@pytest.mark.unit
def test_fold_index_is_sequential_from_zero() -> None:
    folds = build_walk_forward_folds(
        start_date=date(2025, 1, 1),
        end_date=date(2026, 6, 30),
        min_train_days=180,
        test_days=60,
        n_folds=4,
    )
    assert [f.fold_index for f in folds] == list(range(len(folds)))


@pytest.mark.unit
def test_rejects_non_positive_parameters() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_walk_forward_folds(
            start_date=date(2025, 1, 1),
            end_date=date(2026, 1, 1),
            min_train_days=0,
            test_days=60,
            n_folds=4,
        )
