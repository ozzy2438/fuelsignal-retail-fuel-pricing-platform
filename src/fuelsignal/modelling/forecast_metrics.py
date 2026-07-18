"""Metrics and market-phase classification for the seven-day price forecast.

Kept as pure functions (no pandas) so the edge cases - zero actual prices for
MAPE, a zero denominator for WAPE, ties in directional accuracy - are pinned
down by unit tests rather than left to library defaults.
"""

from __future__ import annotations


def classify_market_phase(jump_today: bool, market_daily_change_cpl: float | None) -> str:
    """One of "jump", "decline", "other" for a single market-day.

    "jump" wins over "decline": a day flagged as a jump event by
    gold_price_jump_labels is reported as a jump phase even in the unusual
    case where market_daily_change_cpl is not itself positive that day.
    """
    if jump_today:
        return "jump"
    if market_daily_change_cpl is not None and market_daily_change_cpl < 0:
        return "decline"
    return "other"


def mape(actual: list[float], predicted: list[float]) -> float | None:
    """Mean absolute percentage error, skipping any pair with actual == 0
    (undefined percentage). Returns None if every pair is skipped.
    """
    errors = [abs((a - p) / a) for a, p in zip(actual, predicted, strict=True) if a != 0]
    if not errors:
        return None
    return sum(errors) / len(errors) * 100.0


def wape(actual: list[float], predicted: list[float]) -> float | None:
    """Weighted absolute percentage error: sum(|actual-predicted|) / sum(|actual|).

    Returns None when every actual value is zero (denominator is zero).
    """
    denominator = sum(abs(a) for a in actual)
    if denominator == 0:
        return None
    numerator = sum(abs(a - p) for a, p in zip(actual, predicted, strict=True))
    return numerator / denominator * 100.0


def directional_accuracy(
    last_observed: list[float], actual: list[float], predicted: list[float]
) -> float | None:
    """Fraction of predictions whose sign of change from `last_observed`
    matches the actual sign of change.

    A "no change" prediction (sign 0) only counts as correct against a
    genuinely flat actual outcome, never as a free pass against a small move
    in either direction. Returns None for empty input.
    """
    if not actual:
        return None
    correct = 0
    for base, act, pred in zip(last_observed, actual, predicted, strict=True):
        if _sign(act - base) == _sign(pred - base):
            correct += 1
    return correct / len(actual)


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0
