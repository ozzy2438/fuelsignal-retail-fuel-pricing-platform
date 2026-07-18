"""Walk-forward (expanding-window) time-based validation splits.

Never a random split - every fold's test period is strictly after its train period,
matching how the pipeline would actually be retrained and scored in production. See
docs/validation-methodology.md for why random splitting leaks in an autocorrelated
price series.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class WalkForwardFold:
    """One expanding-window fold. Train always starts at the series' start date."""

    fold_index: int
    train_start: date
    train_end: date  # inclusive
    test_start: date
    test_end: date  # inclusive


def build_walk_forward_folds(
    start_date: date,
    end_date: date,
    min_train_days: int,
    test_days: int,
    n_folds: int,
) -> list[WalkForwardFold]:
    """Build expanding-window folds within [start_date, end_date].

    Fold 0 trains on [start_date, start_date + min_train_days) and tests on the
    following test_days. Fold 1 trains on everything up to and including fold 0's
    test window, and so on - train only ever grows, test only ever moves forward, and
    a fold's test period never overlaps its own or any prior fold's train period.

    Returns fewer than n_folds (possibly zero) if the available date range cannot
    support them all - never extends test past end_date.
    """
    if min_train_days <= 0 or test_days <= 0 or n_folds <= 0:
        raise ValueError("min_train_days, test_days, and n_folds must all be positive")

    folds: list[WalkForwardFold] = []
    train_end = start_date + timedelta(days=min_train_days - 1)
    for fold_index in range(n_folds):
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days - 1)
        if test_end > end_date:
            break
        folds.append(WalkForwardFold(fold_index, start_date, train_end, test_start, test_end))
        train_end = test_end
    return folds
