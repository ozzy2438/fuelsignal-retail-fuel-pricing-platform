# Seven-Day Price Forecast (Week 2 Phase 3, Part 2)

Trained and evaluated live 2026-07-18 against the Databricks workspace by
`scripts/forecast_prices.py`. Tracked in a new Databricks-hosted MLflow experiment,
`/Shared/fuelsignal-price-forecast`, run ID `b2d74cefdb0d4c81ac27c9366deb1685`. Full
per-fuel, per-horizon, per-phase results are logged as the MLflow artifact
`price_forecast_results.json` and mirrored at `config/price_forecast_results.json`.

> **Scope boundary**: this document reports forecast accuracy only. No pricing policy,
> HOLD/FOLLOW/LEAD decision, or commercial-impact claim is made here - see
> `assumptions-and-limitations.md`.

## 1. Scope of this iteration

- **Fuel types**: U91, E10, P95, P98, DL, PDL - LPG/E85/B20 excluded, same rationale as
  the jump model (`config/project.yml -> price_forecast.included_fuel_types`).
- **Target**: the market median price (`market_median_price_cpl` from
  `gold_price_jump_labels`), forecast at horizons of **1, 3 and 7 days ahead**, at
  market grain (fuel_type x date - not per-station).
- **Data pulled**: 2,929 rows (6 fuel types x ~489 market-days), joined against
  date-level context (day_of_week, is_public_holiday, tgp_7d_change_cpl) aggregated
  from `gold_market_cycle_features`.

## 2. Features and leakage controls

Trailing-only rolling features engineered independently per fuel type
(`build_feature_frame`, `scripts/forecast_prices.py`): 7-day and 14-day rolling
mean/std/min/max of the market median price, `market_daily_change_cpl`, days-since-
last-jump (`days_since_last_jump_series`, reused from the jump model - `None`/NaN
before that fuel type's first observed jump, never guessed as 0), plus
`tgp_7d_change_cpl`, `day_of_week`, `is_public_holiday`. Every rolling window is
`min_periods=1` and inclusive of the current day only - the day being forecast from
never sees its own future. `tgp_7d_change_cpl` is permanently null for non-U91/DL fuel
types (established in Phase 1); LightGBM handles this via its native missing-value
splitting rather than the row being dropped.

Horizon targets (`target_price_h{1,3,7}`) are the market median price shifted forward
by the horizon within each fuel type's own ordered series - by construction, only
usable as a training *label*, never joined back in as a feature.

## 3. Validation methodology

Time-based walk-forward only (`build_walk_forward_folds`, the same 4-fold expanding-
window scheme as the jump model - `config/project.yml -> modelling`). Because the
market-level series only has ~489 days of history (versus the jump model's row-grain
volume), all 4 folds share a fixed 180-day training floor that only grows fold over
fold:

| Fold | Train | Test |
|---|---|---|
| 0 | 2025-01-01 → 2025-06-29 | 2025-06-30 → 2025-08-28 |
| 1 | 2025-01-01 → 2025-08-28 | 2025-08-29 → 2025-10-27 |
| 2 | 2025-01-01 → 2025-10-27 | 2025-10-28 → 2025-12-26 |
| 3 | 2025-01-01 → 2025-12-26 | 2025-12-27 → 2026-02-24 |

One LightGBM regressor is trained per (fold, horizon) - not per fuel type - with
`fuel_type` as a native categorical feature, matching the jump model's approach. Test
predictions are pooled across all 4 folds before computing final metrics (1,260 pooled
test rows per horizon: 210 per fuel type x 6 fuel types).

## 4. Four methods compared

- **Persistence**: tomorrow's/day-3's/day-7's price = today's price.
- **7-day moving average**: mean of the trailing 7 prices (min_periods=1).
- **Linear trend**: ordinary least squares over the trailing 14 prices, extrapolated
  forward by the horizon.
- **LightGBM**: the regressor described in §2-3.

All three non-ML baselines and the LightGBM model use only price history available up
to and including the prediction day - `src/fuelsignal/modelling/forecast_baselines.py`
(8 unit tests) computes each baseline as a pure function of a trailing price list, no
pandas, so the "only past information" boundary is enforced independently of the
feature-engineering code that builds the main dataset.

## 5. Results by horizon (pooled across all 4 folds, all 6 fuel types)

### 5a. Day 1

| Fuel | Method | MAE | WAPE % | Directional accuracy |
|---|---|---|---|---|
| U91 | Persistence | 3.32 | 1.89 | **0.105** |
| U91 | LightGBM | 3.77 | 2.15 | **0.514** |
| E10 | Persistence | 3.60 | 2.07 | 0.129 |
| E10 | LightGBM | 4.45 | 2.56 | **0.495** |
| P98 | Persistence | 3.40 | 1.70 | 0.114 |
| P98 | LightGBM | 3.85 | 1.93 | **0.543** |

(Full table for all 6 fuel types x 4 methods in `config/price_forecast_results.json`.)

**At day 1, persistence has the lowest MAE/WAPE for every fuel type** - day-to-day
market median price moves are small enough that "no change" is a strong point
forecast by absolute error. But persistence's directional accuracy is uniformly
terrible (0.03-0.13: barely better than never getting the direction right, because it
never predicts a direction at all) - LightGBM's directional accuracy is 4-10x higher
at this horizon (0.43-0.65) for a modest MAE cost. Which method is "better" at day 1
depends on whether the consuming use case needs a precise price level or a reliable
up/down signal - see §7.

### 5b. Day 3

| Fuel | Method | MAE | WAPE % | Directional accuracy |
|---|---|---|---|---|
| U91 | LightGBM | **5.38** | **3.07** | **0.657** |
| U91 | Persistence | 5.77 | 3.29 | 0.038 |
| P95 | LightGBM | **5.53** | **2.87** | **0.671** |
| P95 | Persistence | 6.33 | 3.28 | 0.029 |
| P98 | LightGBM | **5.18** | **2.59** | **0.724** |
| P98 | Persistence | 6.09 | 3.05 | 0.033 |

**LightGBM wins on every metric for every fuel type at day 3** except PDL, where the
moving average edges it out on MAE/WAPE (3.65 vs 4.46) while LightGBM still leads on
directional accuracy (0.600 vs 0.652 - close). The trailing-only rolling features start
paying off once the horizon is long enough that "no change" stops being a good enough
answer.

### 5c. Day 7

| Fuel | Method | MAE | WAPE % | Directional accuracy |
|---|---|---|---|---|
| U91 | LightGBM | **7.65** | **4.36** | **0.724** |
| U91 | Persistence | 10.00 | 5.70 | 0.014 |
| U91 | Linear trend | 16.35 | 9.32 | 0.333 |
| E10 | LightGBM | **7.68** | **4.43** | **0.738** |
| E10 | Persistence | 10.74 | 6.19 | 0.014 |
| E10 | Linear trend | 17.52 | 10.10 | 0.333 |
| P98 | LightGBM | **7.14** | **3.57** | **0.757** |
| P98 | Persistence | 10.47 | 5.24 | 0.038 |
| P98 | Linear trend | 17.15 | 8.58 | 0.329 |

**LightGBM wins decisively at day 7 for every fuel type** - roughly 30% lower WAPE than
persistence and 55-70% lower than linear trend, with directional accuracy 0.50-0.76
versus persistence's near-zero (0.01-0.09). The 14-day linear trend is the worst method
at this horizon by a wide margin: extrapolating a short trend 7 days out amplifies
whatever noise was in the last two weeks rather than capturing the market's actual
mean-reverting cycle behavior (`jump-label-definition.md`).

## 6. Performance during jump and decline phases

Market phase is classified per target date (`classify_market_phase`,
`src/fuelsignal/modelling/forecast_metrics.py`, 4 unit tests): **jump** if
`gold_price_jump_labels.jump_today` is true that day, else **decline** if
`market_daily_change_cpl < 0`, else **other**. Pooled MAE across all 6 fuel types, by
method and phase:

| Horizon | Method | Jump-phase MAE (n) | Decline-phase MAE (n) | Other-phase MAE (n) |
|---|---|---|---|---|
| Day 1 | Persistence | 11.64 (112) | 3.56 (660) | 2.06 (488) |
| Day 1 | LightGBM | **9.03** (112) | 3.53 (660) | 3.09 (488) |
| Day 3 | Persistence | 10.45 (115) | 5.68 (659) | 4.75 (486) |
| Day 3 | LightGBM | **8.76** (115) | 5.07 (659) | 4.61 (486) |
| Day 7 | Persistence | 12.86 (123) | 8.42 (663) | 7.24 (474) |
| Day 7 | Linear trend | 16.43 (123) | 13.42 (663) | 12.21 (474) |
| Day 7 | LightGBM | **9.74** (123) | **6.35** (663) | **6.04** (474) |

**Every method's error is highest during jump phases** - expected, since a jump is by
definition an unusually large price move that no trailing-window method can fully
anticipate the size of. LightGBM's advantage over the baselines is largest exactly
where it matters most: at day 7, LightGBM's jump-phase MAE (9.74) is lower than
persistence's *decline*-phase MAE (8.42), a phase that is inherently easier to predict.

## 7. Selected method per horizon

Logged as MLflow run tags `best_method_h{1,3,7}` (lowest average WAPE across fuel
types, pooled test predictions): **day 1 → persistence, day 3 → LightGBM, day 7 →
LightGBM**. This is a WAPE-only selection, not a recommendation - §5a's directional-
accuracy gap means a "day 1 forecast" consumer that cares about direction, not just
point-price accuracy, would reasonably choose LightGBM at every horizon despite
persistence's lower WAPE at day 1. No method choice made here constitutes a pricing
decision; see §8.

## 8. What this phase does NOT include

- No pricing policy (hold/follow/lead) or backtest of one.
- No commercial-impact, revenue, or margin-uplift claim.
- No per-fuel-type separate LightGBM models (one shared model per horizon per fold,
  `fuel_type` as a categorical feature, matching the jump model's approach) - a
  per-fuel-type model is a candidate for a future iteration if a fuel type's error
  profile warrants it.
- LPG, E85, B20 remain excluded (too little raw history, same as the jump model).
