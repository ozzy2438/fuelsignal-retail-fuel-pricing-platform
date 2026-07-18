# FuelSignal Feature Engineering (Gold Layer)

This document describes how the Gold layer is built from Silver, the grain of every
table, the daily price construction rule, competitor/TGP join rules, and the leakage
controls behind every rolling feature. Built and validated live against Databricks on
2026-07-18 by `scripts/run_gold_pipeline.py`.

## 1. Canonical grain

**station_id × fuel_type × market_date** for every per-station Gold table
(`gold_station_daily_market`, `gold_market_cycle_features`, `gold_indicative_margin`,
`gold_daily_pricing_inputs`). `gold_competitor_positioning` adds a fourth grain column,
`competitor_station_id` (one row per competitor pair per day - a detailed drill-down
table, not the aggregate stats). `gold_price_jump_labels` grains by **fuel_type ×
market_date only** - a price jump is a market-wide event, not a per-station one; see
`jump-label-definition.md`.

Grain is enforced by construction (`GROUP BY`/window `PARTITION BY` on exactly these
columns) and verified live on every run: zero duplicate keys across all five keyed
tables as of 2026-07-18 (see `data-quality.md`).

## 2. "Market" vs "local market" - two different denominators

This is the single most important naming distinction in the Gold layer:

- **`market_median_price_cpl`** = the statewide daily median retail price for a
  `fuel_type`, across every matched station in NSW that day. Computed once in
  `gold_station_daily_market` and reused everywhere downstream (cycle features, jump
  labels) rather than recomputed.
- **`local_competitor_*` / `station_price_percentile` / `rank_within_local_market`** =
  computed only among the station's own 5km competitor set (from
  `silver_competitor_pairs`) plus itself, using only competitors with a **valid price
  for the same fuel type on the same date** - no forward-fill, no back-fill.

A station can be well below the local competitor median while sitting above the
statewide market median, and vice versa - both numbers are provided because they answer
different commercial questions (am I competitive against my immediate neighbours? vs.
where do I sit in the whole state's price cycle?).

`silver_competitor_pairs` stores each pair once (`station_id < competitor_station_id`),
so every query here expands it to a symmetric adjacency list before use.

## 3. Daily price construction rule

The bulk archive can carry multiple price observations per station per day. The
candidate rule from the task brief - "the final valid observation available on that
date" - is what `daily_close_price_cpl` implements, and it is the price used everywhere
else in the Gold layer (competitor positioning, TGP margin, rolling features, market
median). It is computed as `max_by(price_cpl, observed_at)` - the price at the latest
`observed_at` timestamp for that station/fuel/date.

Retained alongside it, per row:

| Column | Definition |
|---|---|
| `daily_open_price_cpl` | Price at the earliest `observed_at` that day (`min_by`) |
| `daily_close_price_cpl` | Price at the latest `observed_at` that day - the canonical daily price |
| `daily_min_price_cpl` / `daily_max_price_cpl` | Min/max of all observations that day |
| `daily_observation_count` | How many raw observations fed this row |
| `last_observed_at` | Timestamp of the close price |

## 4. Competitor positioning (`gold_station_daily_market` aggregate columns)

For each station/fuel/date, joined against the symmetric competitor adjacency list,
restricted to competitors with a same-day, same-fuel-type `daily_close_price_cpl`:

- `competitor_count` - `0` when the station has competitors in `silver_competitor_pairs`
  but none reported a price that specific day, distinct from a station with no
  competitors within 5km at all (`silver_station_master` already reports 226 such
  isolated stations - see Week 1 validation).
- `local_competitor_min/max/median/mean_price_cpl`
- `station_vs_competitor_median_cpl` = `daily_close_price_cpl - local_competitor_median_price_cpl`
- `station_price_percentile` = fraction of the local set (competitors only) priced
  strictly below this station, on `[0, 1]`
- `rank_within_local_market` = `1 + count(competitors priced strictly below)` (`1` = cheapest locally)

`gold_competitor_positioning` holds the same join at full grain (one row per
competitor pair per day) for drill-down/audit use, including `is_cheapest_local` and
`rank_in_local_market` per pair.

**Live coverage (2026-07-18):** 86.05% of `gold_station_daily_market` rows have at
least one same-day competitor price (`competitor_count > 0`).

## 5. Terminal gate price (TGP) alignment

- **Terminal/city:** Sydney only, for every NSW station (documented simplification -
  see `assumptions-and-limitations.md`; regional freight-adjusted wholesale costs are
  not captured).
- **Fuel-type mapping:** AIP only publishes ULP and Diesel TGP, mapped to `U91` and
  `DL` respectively (`silver_terminal_gate_prices`). The other seven retail fuel types
  seen in `silver_fuel_prices` (E10, P95, P98, PDL, LPG, E85, B20) have **no TGP
  source** and always get `tgp_cpl = NULL` by construction - not a bug, not
  quarantined, just genuinely unavailable at the wholesale level published by AIP.
- **Join rule - two columns, two rules, so both are visible:**
  - `tgp_cpl` (used for `indicative_margin_cpl`): same-day match if available,
    otherwise the **latest prior date's** TGP for that fuel type ("ASOF" join,
    standard practice for gappy daily price series). `tgp_match_type` records which
    happened (`exact_same_day` / `latest_prior_date` / `unmatched`).
  - `price_tgp_spread_cpl`: same formula but **exact same-day match only** (`NULL`
    when no exact match exists that day) - lets you see how much the ASOF fallback
    changes the result.
- **Weekend/holiday handling:** no special-casing; the ASOF fallback already covers any
  date AIP didn't publish for, weekend or otherwise.
- **Measured unmatched rate (2026-07-18):** 28.87% of `gold_indicative_margin` rows
  have a non-null `tgp_cpl` (U91 + DL only, as expected: 187,212 + 66,664 = 253,876 of
  879,486 rows). Of the TGP-matched rows, 44,832 (17.7%) needed the latest-prior-date
  fallback rather than an exact same-day match.
- Indicative margin range observed: -164.8 to 133.2 cpl, mean 16.3 cpl. **This is an
  indicative margin only** (`retail_price_cpl - tgp_cpl`) - it excludes freight, opex,
  and franchise fees and must never be described as a realised P&L margin.

## 6. Price-cycle features (SQL window functions, trailing-only)

Every feature is computed with `ROWS BETWEEN N PRECEDING AND CURRENT ROW` or `LAG(...)`
- never `FOLLOWING` or `LEAD` - partitioned by `station_id, fuel_type`, ordered by
`market_date`. This makes lookahead structurally impossible for these columns; the Gold
pipeline additionally self-checks its own source code on every run (`run_leakage_checks`
greps the four feature-building methods for `FOLLOWING`/`LEAD` and fails loudly if found).

| Feature | Definition |
|---|---|
| `rolling_7d_min/max/mean/std_price` | `MIN/MAX/AVG/STDDEV(daily_close_price_cpl)` over the trailing 7 rows (this station's own observation sequence, not strictly calendar days if the station has gaps) |
| `rolling_14d_min/max_price`, `rolling_14d_price_change_cpl` | Same, over 14 rows; change = current close minus the close 14 rows back |
| `days_since_local_minimum` | Days since the most recent date where `daily_close_price_cpl` equalled `rolling_14d_min_price` - a trailing-only trough proxy (a true two-sided local minimum would require seeing the day after, which would leak) |
| `days_since_last_detected_jump` | Days since this **station's own** price last rose ≥ the chosen jump threshold (7.0 cpl - see `jump-label-definition.md`) vs the prior day. This is a per-station feature signal, distinct from the market-wide jump **label** |
| `price_position_within_14d_range` | `(close - rolling_14d_min) / (rolling_14d_max - rolling_14d_min)`, `NULL` when the 14d range is zero |
| `market_median_price_cpl`, `market_daily_change_cpl` | Statewide series (see §2), broadcast onto every station row for that fuel/date |
| `tgp_7d_change_cpl` | `tgp_cpl` minus its value 7 rows back |
| `margin_compression_cpl` | `rolling_7d_mean(indicative_margin_cpl) - indicative_margin_cpl` - positive means today's margin sits below its own recent average (a compression signal that can motivate a price rise) |
| `day_of_week`, `is_public_holiday` | Calendar features from `silver_public_holidays` |

## 7. Known data-quality finding: extreme 14-day price swings

10,139 of 879,486 rows (1.15%) show `|rolling_14d_price_change_cpl| > 100 cpl`,
concentrated in `PDL` (5,151) and `DL` (3,337). Spot-checked: these come from
**very-low-observation stations** (`daily_observation_count` of 1-4 for the whole
period) reporting prices near the plausibility ceiling (289.9, 299.9 cpl) that
individually pass the `[80, 300]` bound check but represent an implausible swing when
compared 14 days apart. This is a genuine Silver-level data quality characteristic
(sparse/noisy self-reported prices), not a Gold pipeline bug - the window functions are
computing correctly over what Silver provides. Recommendation for Week 2 Phase 2
(modelling): apply a minimum-observation-count filter (`daily_observation_count`
history ≥ some threshold) before training rather than filtering it out of Gold, so the
finding stays visible for analysis. 603 station/fuel_type pairs have under 14 days of
total history and cannot support a full 14-day rolling window at all.

## 8. Performance approach

The Gold pipeline never re-scans Bronze (1.4M rows). It builds
`gold_station_daily_market` once from `silver_fuel_prices` (the single most expensive
aggregation, ~1.2M rows down to 879,486 station-day-fuel rows), and every other Gold
table reads from that physical table rather than re-aggregating Silver again. Full
live rebuild of all six Gold tables completes in under 3 minutes on the connected
Databricks SQL warehouse. `percent_rank()`/`rank()` are not usable with a custom `ROWS
BETWEEN` frame in Spark SQL (they always rank over the full partition); where a
genuinely trailing-only percentile was needed (`margin_percentile_30d`), it is computed
via a `row_number()`-bounded self-join instead, to avoid silently leaking future
observations into a ranking function.

## 9. Idempotency

Unlike Bronze/Silver (append-only source-of-truth, merged by content hash), Gold is a
fully-derived analytical layer: every column is reproducible from Silver at any time.
"Idempotent" for Gold means the pipeline **fully rebuilds** the same result from the
same Silver input on every run (`DROP TABLE` + recreate from the current schema
definitions, then re-`INSERT`) - verified by running the pipeline twice in this session
and confirming identical row counts and metrics both times.
