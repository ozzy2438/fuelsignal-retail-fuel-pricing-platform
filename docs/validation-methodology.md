# FuelSignal Validation Methodology

## 1. Gold-layer leakage controls (implemented and live-verified 2026-07-18)

**Why this matters:** in a price series with strong autocorrelation, a feature that
accidentally sees tomorrow's price makes a model look far better than it will ever
perform in production - the single most common way a time-series project silently
produces an untrustworthy result.

**Controls in place:**

1. **Structural, not just tested.** Every rolling feature in
   `gold_market_cycle_features` uses `ROWS BETWEEN N PRECEDING AND CURRENT ROW` or
   `LAG(...)` - SQL window frame clauses that are physically incapable of including
   rows after the current one. This is a stronger guarantee than a runtime check: the
   query cannot return a leaked value even if the code has a logic bug elsewhere.
2. **Self-checked on every run.** `scripts/run_gold_pipeline.py::run_leakage_checks`
   reads its own source code and asserts that none of the four feature-building
   methods (`build_station_daily_market`, `build_indicative_margin`,
   `build_market_cycle_features`, `build_daily_pricing_inputs`) contain `FOLLOWING` or
   `LEAD(` - if a future edit introduces one, the pipeline fails loudly rather than
   silently shipping a leaky feature. Verified live: `true` / `[]` (no violations) on
   the 2026-07-18 run.
3. **Labels are physically separated from features.** `gold_price_jump_labels` is a
   different table, at a different grain (`fuel_type × market_date`, not
   `station_id × fuel_type × market_date`), and is the only place `LEAD()` is used -
   confirmed present there (by design) and absent everywhere else.
4. **Duplicate-key checks** on every keyed Gold table (all five: `0` duplicates as of
   2026-07-18) - a duplicate business key is a common source of accidental data
   leakage into aggregates (double-counting a row shifts a rolling mean/stddev).
5. **Competitor and TGP joins are same-day-or-earlier only.** Competitor prices are
   joined on `(station_id, fuel_type, market_date)` exact match only - no forward-fill.
   TGP uses an explicit ASOF join (same day or **latest prior** date, never a future
   date) - see `feature-engineering.md` §5 for the exact join logic and measured
   fallback rate.
6. **Python/SQL cross-check.** The jump-threshold sensitivity logic exists twice:
   once in SQL (live, against the real market-median series) and once in pure,
   unit-tested Python (`src/fuelsignal/gold/jump_labels.py`). The pipeline pulls the
   live `market_daily_change_cpl` series back out of Gold and re-runs it through the
   Python module for every candidate threshold, asserting the event counts match. All
   five candidate thresholds agreed exactly on the 2026-07-18 run.

## 2. Gold data-quality validation

Computed live on every run (`GoldPipeline.write_gold_dq`) and persisted to
`monitoring_data_quality_results`:

- Row count, date range, distinct station/fuel-type coverage per table
- `%` of rows with a valid TGP match, `%` with local competitor coverage
- Implausible-margin count (`indicative_margin_cpl` outside `[-50, 100]` cpl) -
  reported as both a percentage **and** a raw count, since a rare-but-real issue can
  round to `0.00%` and look clean at low precision (24 rows / 879,486 = 0.0027%, easy
  to miss if only the rounded percentage is read)
- Extreme 14-day price change count (`|rolling_14d_price_change_cpl| > 100` cpl)
- Jump-label counts and frequency by fuel type
- Station/fuel_type pairs with under 14 days of history (cannot support a full 14-day
  rolling window)

See `data-quality.md` for the full live results table and `feature-engineering.md` §7
for the extreme-price-change finding's root cause.

## 3. Time-based validation (planned for Week 2 Phase 2/3 - not yet executed)

No model has been trained yet, so no walk-forward backtest results exist. The planned
methodology, stated here so it is agreed before any model code is written:

Random train/test splitting would leak: a mid-cycle Wednesday in the training set and
the same cycle's Thursday in the test set are strongly correlated (same price cycle,
adjacent days), so a random split lets the model "see" a near-duplicate of its test
answer during training, producing an artificially good validation score that will not
reproduce in production. The plan is **walk-forward (rolling-origin) backtesting**:
train on all data up to date D, evaluate on the following window, advance D, repeat -
matching how the pipeline will actually be retrained in production and giving an
honest estimate of live performance. `gold_daily_pricing_inputs` already carries
`market_date` as an explicit column specifically so this splitting can be enforced
downstream.

Metric choice will follow the data: MAPE for price-level forecasts (interpretable to a
commercial stakeholder as "average % error"), reported alongside RMSE since MAPE
penalizes under- and over-forecasts asymmetrically; precision/recall and lead time for
the jump classifier, since MAPE is meaningless for a binary target.

## 4. What "passing" means for this phase

This phase (Gold population) is validated, not the eventual model. "Passing" here
means: grain is unique, leakage controls hold, joins behave as documented, thresholds
are empirically justified rather than assumed, and every DQ metric is measured and
reported - not that any number is "good" in a predictive sense. No model-performance or
commercial-impact claim is made in this document or anywhere in the Gold layer.
