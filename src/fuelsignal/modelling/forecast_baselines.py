"""Non-ML baselines for the seven-day market-level price forecast.

Three transparent baselines compared against the LightGBM regressor in
scripts/forecast_prices.py: persistence (last observed price), a trailing
moving average, and a simple linear trend extrapolated forward. All three use
only prices observed up to and including the prediction day - never a future
value.
"""

from __future__ import annotations


def persistence_forecast(prices: list[float]) -> float | None:
    """Predict every future horizon as the last observed price.

    Returns None when there is no observed price yet.
    """
    if not prices:
        return None
    return prices[-1]


def moving_average_forecast(prices: list[float], window: int) -> float | None:
    """Predict every future horizon as the mean of the trailing `window` prices.

    Uses fewer than `window` observations when that's all history has -
    never looks forward. Returns None when there is no price history at all.
    """
    if not prices:
        return None
    tail = prices[-window:]
    return sum(tail) / len(tail)


def linear_trend_forecast(prices: list[float], window: int, horizon_days: int) -> float | None:
    """Fit an ordinary-least-squares line over the trailing `window` prices
    (x = 0..n-1, y = price) and extrapolate `horizon_days` steps past the
    last observed point.

    Returns None when fewer than two points are available to fit a line (a
    single point has no defined slope).
    """
    tail = prices[-window:]
    n = len(tail)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(tail) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, tail, strict=True))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return tail[-1]
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x
    return intercept + slope * (n - 1 + horizon_days)
