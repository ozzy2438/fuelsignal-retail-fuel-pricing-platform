# FuelSignal Price-Jump Label Definition

Empirically validated 2026-07-18 against the live 18-month FuelCheck archive
(Jan 2025 - Jun 2026). Implemented in `src/fuelsignal/gold/jump_labels.py` (pure
Python, unit-tested) and `scripts/run_gold_pipeline.py::build_jump_labels` (live SQL);
the pipeline cross-checks both against each other on every run and reports
`python_sql_cross_check` in its output.

## Definition

A **jump** is a day-over-day increase in the **statewide market median retail price**
for a `fuel_type` of at least a threshold, in cents per litre:

```
market_daily_change_cpl = market_median_price_cpl - market_median_price_cpl(previous day)
jump_today = market_daily_change_cpl >= threshold_cpl
```

`jump_today` is always `false` on the first day of a series (no prior day to compare).
This is a **market-level** definition (grain: `fuel_type × market_date`), not a
per-station one - a price jump is what the whole market's price leader/followers do,
not what one station does (that is the separate, per-station
`days_since_last_detected_jump` **feature** in `gold_market_cycle_features` - see
`feature-engineering.md` §6).

## Threshold: empirically chosen, not hardcoded

Candidates tested: **3, 5, 7, 10, 15 cpl** (`config/project.yml` →
`jump_detection.candidate_jump_thresholds_cpl`), against the live market-median series
for every fuel type present in Silver.

### Sensitivity table (U91, reference fuel type, 489 market-days)

| Threshold (cpl) | Event count | Event frequency | Median magnitude when triggered | Min gap between events (days) |
|---|---|---|---|---|
| 3.0 | 92 | 18.8% | 5.7 | 1 |
| 5.0 | 58 | 11.9% | 7.0 | 1 |
| **7.0** | **31** | **6.3%** | **10.0** | **2** |
| 10.0 | 16 | 3.3% | 13.4 | 2 |
| 15.0 | 7 | 1.4% | 24.0 | 9 |

Day-over-day change stats (U91): stddev 5.17 cpl, mean absolute change 3.19 cpl.

### Sensitivity by fuel type at the 7 cpl candidate

| Fuel type | Total days | Event count | Event frequency |
|---|---|---|---|
| U91 | 489 | 31 | 6.3% |
| E10 | 489 | 39 | 8.0% |
| P98 | 489 | 35 | 7.2% |
| P95 | 489 | 35 | 7.2% |
| DL | 487 | 44 | 9.0% |
| PDL | 486 | 49 | 10.1% |
| LPG | 456 | 94 | 20.6% |
| E85 | 146 | 43 | 29.5% |
| B20 | 5 | 1 | 20.0% |

LPG, E85, and B20 show much higher and less stable event frequencies - all three have
far fewer total price observations in Silver (LPG 2,363, E85 401, B20 8 raw rows
platform-wide; see Week 1 validation), so their "market median" series is thin and
noisy. **B20 and E85 in particular do not have enough history to treat their jump
labels as meaningful** - flag for exclusion or a wider evaluation window in Week 2
Phase 2.

### Why 7.0 cpl, not the previous 5.0 cpl default

- At 3-5 cpl, ~12-19% of days qualify as a "jump" - too frequent to represent the
  sharp, discrete price-leader move the commercial problem is about; day-to-day change
  has a standard deviation of ~5.2 cpl, so a 5 cpl threshold is within one standard
  deviation of ordinary noise.
- At 7 cpl, event frequency drops to a sparse, distinct 6-8% of days across the four
  primary fuel types (U91/E10/P95/P98), with median triggered magnitude (~10-11 cpl)
  clearly separated from the threshold floor - a sign these are real distinct events,
  not values clustered just above an arbitrary cutoff.
- At 10-15 cpl, events become too sparse and would likely miss genuine smaller
  leader-follower jumps.
- Minimum gap between events at 7 cpl is 2 days (not 1), reducing (but not eliminating)
  the chance of double-counting a single real jump split across two noisy days; this
  characteristic is disclosed, not hidden.

`config/project.yml` → `jump_detection.min_jump_cpl` is now `7.0` (previously `5.0`),
documented inline with this reasoning. It remains configurable, and the candidate list
is preserved so this analysis can be rerun if more history becomes available.

## Labels (target columns, not features)

Stored only in `gold_price_jump_labels` (grain `fuel_type × market_date`) - **never**
joined into `gold_daily_pricing_inputs` or any other feature table:

| Column | Definition | Uses future info? |
|---|---|---|
| `jump_today` | As defined above | No |
| `jump_within_24h` | `jump_today` on the next calendar day | **Yes** (by design - it's a target) |
| `jump_within_48h` | `jump_today` on the next day OR the day after | **Yes** (by design) |
| `jump_threshold_cpl` | The threshold actually used to compute this row (7.0) | - |

`jump_within_24h`/`jump_within_48h` are implemented with `LEAD()` in SQL and explicit
forward indexing in the Python module - this is the one place in the whole Gold layer
where looking forward is correct and intentional. `gold_daily_pricing_inputs` (the
feature table) is verified live to be fully joinable to `gold_price_jump_labels` on
`(fuel_type, market_date)` with no row loss (879,486/879,486), but the join is never
performed inside the Gold pipeline itself - it is left for the modelling step, which
must do it explicitly and knowingly.

## Live results (2026-07-18, threshold = 7.0 cpl)

| Fuel type | Jump days | Total days |
|---|---|---|
| U91 | 31 | 489 |
| E10 | 39 | 489 |
| P98 | 35 | 489 |
| P95 | 35 | 489 |
| DL | 44 | 487 |
| PDL | 49 | 486 |
| LPG | 94 | 456 |
| E85 | 43 | 146 |
| B20 | 1 | 5 |

`gold_price_jump_labels` totals 3,536 rows (sum of `total_days` above, one row per
fuel_type/date combination present in the archive).
