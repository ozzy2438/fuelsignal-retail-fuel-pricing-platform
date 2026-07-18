# FuelSignal Model-Eligibility Filter

Computed and persisted live 2026-07-18 by `scripts/run_model_eligibility.py` into
`gold_model_eligibility`, before any model training. Decision logic:
`src/fuelsignal/modelling/eligibility.py` (pure Python, unit-tested).

## Why a filter is needed before training

A station x fuel_type series with very few observations cannot support a 14-day
rolling feature, let alone a walk-forward split with a 180-day minimum training
window. A series dominated by implausible price swings (see
`feature-engineering.md` §7) would teach the model noise. Both problems need to be
decided explicitly and recorded, not discovered silently inside a training run.

## Two independent, additive criteria

A series (`station_id x fuel_type`) is **excluded** if either holds:

1. **`total_observations < min_observations_per_series`** (default 30, i.e. roughly
   one month of daily-ish observations over the 18-month archive).
2. **`extreme_change_rate > max_extreme_change_rate`** (default 0.10, i.e. more than
   10% of the series' `gold_market_cycle_features` rows show an implausible
   `|rolling_14d_price_change_cpl| > 100` cpl swing).

Both reasons are recorded when both apply (`exclusion_reason =
"insufficient_observations+extreme_price_volatility"`) - the audit table never hides a
second problem behind the first one found.

## Why these threshold values (empirical basis, 2026-07-18)

Observation-count distribution across the 8,846 station x fuel_type series for the six
approved fuel types:

| Threshold | Series excluded | % of total |
|---|---|---|
| < 14 | 342 | 3.9% |
| < 30 | 1,043 | 11.8% |
| < 60 | 2,937 | 33.2% |
| < 90 | 4,937 | 55.8% |

30 was chosen as the floor: 60 would discard a third of all series, which is too
aggressive for a first model iteration built on only 18 months of history; 30 retains
~88% of series while still excluding series too short to support the 14-day rolling
features every downstream row depends on.

Extreme-change-rate distribution:

| Threshold | Series excluded | % of total |
|---|---|---|
| Any extreme event (`rate > 0`) | 3,005 | 34.0% |
| `rate > 5%` | 1,202 | 13.6% |
| `rate > 10%` | 399 | 4.5% |
| `rate > 20%` | 33 | 0.4% |

`rate > 0` catches over a third of all series - too common to be disqualifying on its
own (a single one-off bad observation among hundreds of good ones shouldn't sink an
otherwise-usable series). 10% was chosen as a defensible middle point: it isolates a
small, genuinely noisy minority (4.5% of series - a spot-checked example had 15
extreme swings in only 61 observations, a 24.6% rate) without discarding series that
merely had one bad day.

## Live results (2026-07-18)

| Metric | Value |
|---|---|
| Total series considered (6 fuel types) | 8,846 |
| Eligible series | 7,429 (84.0%) |
| Excluded series | 1,417 (16.0%) |
| — insufficient observations only | 1,018 |
| — extreme volatility only | 374 |
| — both reasons | 25 |
| `gold_daily_pricing_inputs` rows retained for training | 839,906 of 876,944 target-fuel rows (95.8%) |

Row retention (95.8%) is notably higher than series retention (84.0%) because excluded
series are disproportionately low-observation ones - they contribute few rows each.

## Auditability

`gold_model_eligibility` holds **every** series considered, eligible and excluded
alike, with its raw stats, the exact thresholds used, and `evaluated_at`/
`_pipeline_run_id` for traceability. Nothing is deleted from Gold; excluded series
remain fully queryable in `gold_daily_pricing_inputs` and every other Gold table -
`gold_model_eligibility` is a flag and a reason, not a filtered copy, so re-running
`scripts/train_jump_model.py` (or a future iteration with different thresholds)
never needs to re-derive what was excluded and why.
