# FuelSignal Data Contracts

## Overview

This document defines the grain, primary keys, expected fields, and known limitations
for each table in the FuelSignal platform.

---

## Bronze Layer

### bronze_fuelcheck_prices_raw
- **Grain**: One row per station per fuel type per price observation
- **Primary Key**: `_source_record_hash` (deduplication key)
- **Immutability**: Append-only; raw data never modified
- **Metadata**: `_ingested_at`, `_source_name`, `_source_url`, `_source_file`, `_pipeline_run_id`

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
- **Quality**: Prices within [80, 300] cpl; coordinates within NSW
- **Limitations**: Depends on source data availability

### silver_station_master
- **Grain**: One row per unique station
- **Primary Key**: `station_id`
- **Key Generation**: Official `station_code` where available; SHA-256 hash of (name + address) as fallback
- **Limitations**: Brand normalization is rule-based; some edge cases possible

### silver_terminal_gate_prices
- **Grain**: One row per `tgp_date` per `terminal` per `fuel_type`
- **Primary Key**: (`tgp_date`, `terminal`, `fuel_type`)
- **Quality**: TGP must be positive and within [60, 250] cpl

### silver_competitor_pairs
- **Grain**: One row per directed station pair
- **Primary Key**: (`station_id`, `competitor_station_id`)
- **Rules**: Within 5km (Haversine); no self-pairs; no duplicate reversed pairs
- **Limitations**: Static radius; doesn't account for roads or driving time

### silver_public_holidays
- **Grain**: One row per holiday date
- **Primary Key**: (`holiday_date`, `holiday_name`)

---

## Gold Layer

### gold_daily_pricing_inputs
- **Grain**: One row per `station_id` per `fuel_type` per `market_date`
- **Primary Key**: (`station_id`, `fuel_type`, `market_date`)
- **Contains**: All features needed for ML model input
- **Limitations**:
  - `days_since_last_jump` depends on jump detection definition (>= 5 cpl increase)
  - Jump detection has NOT been formally validated against ACCC definitions yet
  - Margin requires TGP data to be available for the matching date/city

### gold_market_cycle_features
- **Window Functions Used**:
  - `MIN/MAX/AVG OVER (ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)` for 7-day rolling
  - `LAG(price, 14)` for 14-day change
  - `STDDEV OVER (ROWS 13 PRECEDING)` for volatility
  - `SUM(is_jump) ... ROWS UNBOUNDED PRECEDING` for jump group tracking
  - `ROW_NUMBER() OVER (PARTITION BY jump_group)` for days-since-jump

### gold_indicative_margin
- **Calculation**: `retail_price_cpl - tgp_cpl`
- **Important**: This is an INDICATIVE margin only
- **Missing**: Transport costs, operating costs, franchise fees, volume discounts
- **Limitation**: Not a true P&L margin

---

## Known Limitations

1. Price jump detection uses a simple threshold (>= 5 cpl daily increase)
2. Competitor radius is fixed at 5km; real competition may vary
3. TGP matching uses Sydney terminal; regional stations may have different wholesale costs
4. No adjustment for fuel type cross-subsidisation
5. Holiday effect is binary; doesn't account for holiday proximity
