# FuelSignal Data Contracts

## Overview

This document defines the grain, primary keys, expected fields, and known limitations
for each table in the FuelSignal platform.

---

## Bronze Layer

### bronze_fuelcheck_prices_raw
- **Grain**: One row per station per fuel type per price observation
- **Primary Key**: `_source_record_hash` (deduplication key; embeds name/address/postcode/
  brand/fuel/date/price, so a corrected re-parse of the same source file produces new
  hashes rather than silently colliding with bad historical rows)
- **Immutability**: Append-only; raw data never modified
- **Metadata**: `_ingested_at`, `_source_name`, `_source_url`, `_source_file`, `_pipeline_run_id`
- **Coordinates**: always NULL - the bulk archive never carries them (see data-sources.md).
  `latitude`/`longitude` are only ever populated in Silver, via the address+postcode
  crosswalk against `nsw_fuelcheck_api_reference` rows in `bronze_fuelcheck_stations_raw`.

### bronze_fuelcheck_stations_raw
- **Grain**: One row per distinct station identity **per source** - this table holds two
  populations distinguished by `_source_name`:
  - `nsw_fuelcheck` - one row per distinct (name, address, postcode, brand) combination
    seen in the bulk price archive. `station_code` here is a **synthetic** SHA-256 hash
    of (name, address, postcode) - the bulk archive has no official code.
  - `nsw_fuelcheck_api_reference` - one row per official station from the live reference
    API. `station_code` here is the **real, NSW-issued** station code, and `latitude`/
    `longitude` are populated.
- **Primary Key**: `_source_record_hash`, scoped within each `_source_name`

### bronze_aip_tgp_raw
- **Grain**: One row per terminal per product per date
- **Primary Key**: `_source_record_hash`
- **Notes**: May include raw HTML snapshot records

### bronze_public_holidays_raw
- **Grain**: One row per holiday per date
- **Primary Key**: `_source_record_hash`

---

## Silver Layer

### silver_fuel_prices
- **Grain**: One row per `station_id` per `fuel_type` per `observed_at`
- **Primary Key**: (`station_id`, `fuel_type`, `observed_at`)
- **Data Types**: All typed and validated
- **Quality**: Prices within [80, 300] cpl; `station_id` only populated when the row's
  address+postcode resolved to a coordinate-bearing `silver_station_master` row
- **Live volume (2026-07-18, 18-month archive Jan 2025 - Jun 2026)**: 1,197,046 rows,
  ~84% of the 1,423,296 bronze price rows for the period (the remainder is quarantined
  in `silver_data_quality_issues`, predominantly `fuelcheck_station_unmatched`)
- **Limitations**: Depends on source data availability and the live reference API's
  current snapshot for coordinate matching

### silver_station_master
- **Grain**: One row per unique official station
- **Primary Key**: `station_id` (SHA-256 of the official station code)
- **Key Generation**: Official `station_code` from the live FuelCheck reference API only -
  the bulk archive has no official code, so it is never used to derive `station_id`
- **Live volume (2026-07-18)**: 3098 rows, 0 null coordinates, all within NSW bounds
  (lat -37.16 to -28.18, lon 141.43 to 153.62)
- **Limitations**: Brand normalization is rule-based; some edge cases possible; only
  covers the live reference API's current snapshot (closed/rebranded stations from the
  historical archive that no longer appear in the live feed have no coordinates)

### silver_terminal_gate_prices
- **Grain**: One row per `tgp_date` per `terminal` per `fuel_type`
- **Primary Key**: (`tgp_date`, `terminal`, `fuel_type`)
- **Quality**: TGP must be positive and within [60, 250] cpl
- **Live volume (2026-07-18)**: 82,348 rows spanning 2004-01-01 to 2026-07-17 (5882
  distinct dates) from a single workbook download - no separate historical backfill needed

### silver_competitor_pairs
- **Grain**: One row per undirected station pair (single direction: `station_id <
  competitor_station_id`, since the relationship is symmetric)
- **Primary Key**: (`station_id`, `competitor_station_id`)
- **Rules**: Within 5km (Haversine, with a bounding-box pre-filter); no self-pairs; no
  duplicate pairs
- **Live volume (2026-07-18)**: 51,579 pairs across 3098 stations; 0 self-pairs, 0
  duplicate pairs, max distance 4.9998km; competitors per station: min 1, median 16,
  max 227, 226 stations with zero competitors within 5km (isolated/regional)
- **Limitations**: Static radius; doesn't account for roads or driving time

### silver_public_holidays
- **Grain**: One row per holiday date
- **Primary Key**: (`holiday_date`, `holiday_name`)

---

## Gold Layer

Built and live-validated 2026-07-18 by `scripts/run_gold_pipeline.py`. Full grain,
join-rule, and leakage-control documentation: `feature-engineering.md`. Jump-threshold
methodology: `jump-label-definition.md`. Gold is a **fully-derived** layer - every run
drops and rebuilds all six tables from Silver (see `feature-engineering.md` §9); there
is no incremental merge here the way there is for Bronze/Silver.

### gold_station_daily_market
- **Grain**: `station_id × fuel_type × market_date`. **Primary key**: same three columns.
- **Contains**: Daily open/close/min/max price + observation count (see
  `feature-engineering.md` §3 for the "final valid observation = close price" rule),
  statewide market median, and aggregate local (5km) competitor stats.
- **Live volume (2026-07-18)**: 879,486 rows, 2180 stations, 9 fuel types, 2025-01-01
  to 2026-06-30, 0 duplicate keys. 86.05% of rows have same-day competitor coverage.

### gold_market_cycle_features
- **Grain**: `station_id × fuel_type × market_date`.
- **Window functions used**: `MIN/MAX/AVG/STDDEV(...) OVER (ROWS BETWEEN 6/13
  PRECEDING AND CURRENT ROW)` for 7d/14d rolling stats; `LAG(price, 14)` for 14-day
  change; `MAX(date_flag) OVER (ROWS UNBOUNDED PRECEDING)` for days-since-local-minimum
  and days-since-own-jump (a running-carry-forward pattern, not `ROW_NUMBER() OVER
  (PARTITION BY jump_group)` as in the original scaffold - the jump_group approach
  breaks for `days_since_local_minimum`, which is not a partition-resetting event).
- **Live volume**: 879,486 rows, 0 duplicate keys. 10,139 rows (1.15%) show
  `|rolling_14d_price_change_cpl| > 100` cpl - see `feature-engineering.md` §7 for the
  root cause (sparse, low-observation-count stations, not a pipeline bug).
- **Limitations**: rolling windows are based on each station's own observation
  sequence, not strict calendar days, when a station has reporting gaps.

### gold_indicative_margin
- **Grain**: `station_id × fuel_type × market_date`.
- **Calculation**: `indicative_margin_cpl = daily_close_price_cpl - tgp_cpl` (ASOF-joined
  TGP); `price_tgp_spread_cpl` uses the same formula but an exact-same-day-only TGP match.
- **Live volume**: 879,486 rows, 0 duplicate keys. 28.87% have a non-null `tgp_cpl`
  (U91 + DL only - TGP has no other fuel-type coverage). 24 rows (0.0027%) have a
  margin outside `[-50, 100]` cpl. Margin range: -164.8 to 133.2 cpl, mean 16.3 cpl.
- **Important**: This is an INDICATIVE margin only.
- **Missing**: Transport costs, operating costs, franchise fees, volume discounts.
- **Limitation**: Not a true P&L margin - never describe it as realised profit.

### gold_competitor_positioning
- **Grain**: `station_id × fuel_type × market_date × competitor_station_id` (detailed
  per-pair drill-down; the aggregate competitor stats live on
  `gold_station_daily_market` instead - see `feature-engineering.md` §4).
- **Live volume**: 6,961,790 rows.

### gold_daily_pricing_inputs
- **Grain**: `station_id × fuel_type × market_date`. **Contains**: FEATURES only, no
  labels - combines `gold_station_daily_market` + `gold_market_cycle_features` +
  `gold_indicative_margin`.
- **Live volume**: 879,486 rows, 0 duplicate keys, fully joinable to
  `gold_price_jump_labels` on `(fuel_type, market_date)` (879,486/879,486 rows match).

### gold_price_jump_labels
- **Grain**: `fuel_type × market_date` (market-wide, **not** per-station - see
  `jump-label-definition.md`). **Contains**: TARGET columns only
  (`jump_today`/`jump_within_24h`/`jump_within_48h`) - `jump_within_24h`/`48h` use
  future information by design and must never be joined into a feature table.
- **Live volume**: 3,536 rows. Threshold: 7.0 cpl (empirically chosen - see
  `jump-label-definition.md` for the full sensitivity table across 3/5/7/10/15 cpl
  candidates and all nine fuel types).

---

## Known Limitations

1. Price jump label threshold is 7.0 cpl (empirically chosen from a 3/5/7/10/15 cpl
   sensitivity sweep - not formally validated against ACCC's own cycle definitions)
2. Competitor radius is fixed at 5km; real competition may vary
3. TGP matching uses Sydney terminal for all NSW stations; regional stations may have
   different wholesale costs; only U91/DL have any TGP coverage at all
4. No adjustment for fuel type cross-subsidisation
5. Holiday effect is binary; doesn't account for holiday proximity
6. LPG/E85/B20 jump-label frequencies are unstable due to thin underlying price history
   (2,363 / 401 / 8 raw Silver rows respectively) - not reliable targets yet
