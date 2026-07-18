# FuelSignal Data Quality

## Philosophy

1. **Never silently delete bad records** — quarantine them visibly
2. **Document every rule** — rules defined in config, not buried in code
3. **Measure continuously** — quality metrics per pipeline run
4. **Severity-based actions** — critical = quarantine, warning = flag, info = log

## Quality Checks

### Fuel Prices
| Rule | Severity | Action |
|------|----------|--------|
| fuelcheck_price_bounds (price between [80, 300] cpl) | Error | Quarantine |
| fuelcheck_timestamp_parseable | Error | Quarantine |
| fuelcheck_station_unmatched (address+postcode has no match in silver_station_master) | Error | Quarantine |
| no duplicate (station, fuel, time) | Warning | Deduplicate |

`fuelcheck_station_unmatched` is the practical reason most bronze price rows do not
reach `silver_fuel_prices`: the bulk archive never carries coordinates directly (see
data-sources.md), so every price row depends on its station's normalized
address+postcode resolving to exactly one row in `silver_station_master`. There is no
bronze-level "coordinates missing" rule anymore - that was true of 100% of rows by
construction and was not an actionable signal; the actionable question is whether the
row could be *matched* to a coordinate-bearing station, which this rule answers.

### Terminal Gate Prices
| Rule | Severity | Action |
|------|----------|--------|
| date parseable | Critical | Quarantine |
| terminal identifiable | Critical | Quarantine |
| TGP positive | Critical | Quarantine |
| TGP between [60, 250] cpl | Warning | Flag |
| no duplicate (date, terminal, fuel) | Warning | Deduplicate |

### Station Master (identity crosswalk)

`silver_station_master` is built by deterministically matching the live, official
FuelCheck reference API (which carries coordinates but no price history) against the
bulk price archive (which carries price history but no coordinates), joined on a
normalized `address + postcode` key (`src/fuelsignal/silver/station_matching.py`).
Station **name** is deliberately excluded from the join key - it varies too much
between the two sources (rebrands, abbreviations) to be a reliable join column;
address text is far more stable.

| Rule | Severity | Action |
|------|----------|--------|
| Exactly one official station and one bulk-archive station share a normalized key | - | Insert as `match_method='exact_address_postcode'`, `match_confidence=1.0` |
| Official station has no bulk-archive counterpart | - | Insert as `match_method='reference_only_no_bulk_match'`, `match_confidence=1.0` (still coordinate-bearing and officially sourced) |
| station_match_ambiguous (a key maps to >1 station on either side) | Warning | Quarantine to `silver_data_quality_issues`, never guessed |
| station_unmatched_no_coordinates (bulk station has no official counterpart at all) | Error | Quarantine to `silver_data_quality_issues` - no coordinates are fabricated |
| latitude/longitude NOT NULL | Error | Enforced by table schema - unmatched stations simply do not get a row |

### Gold Layer

Gold DQ metrics are computed live on every `scripts/run_gold_pipeline.py` run and
written to `monitoring_data_quality_results` (severity `info` - these are reported
metrics, not quarantine actions, since Gold is a fully-derived layer rebuilt from
Silver on every run rather than an append-only quarantine target). Full methodology:
`validation-methodology.md`; full root-cause analysis: `feature-engineering.md` §7.

| Metric | Live result (2026-07-18) |
|---|---|
| Row count (6 tables) | `gold_station_daily_market`/`gold_market_cycle_features`/`gold_indicative_margin`/`gold_daily_pricing_inputs`: 879,486 each; `gold_competitor_positioning`: 6,961,790; `gold_price_jump_labels`: 3,536 |
| Date range | 2025-01-01 to 2026-06-30 |
| Distinct stations / fuel types | 2,180 / 9 |
| Duplicate business keys (all 5 keyed tables) | 0 |
| % rows with valid TGP | 28.87% (U91 + DL only, by construction - see data-sources.md) |
| % rows with competitor coverage | 86.05% |
| Implausible margin (outside [-50, 100] cpl) | 24 rows (0.0027%) - reported as both percentage and raw count so a rare issue can't round away to an invisible 0.00% |
| Extreme 14-day price change (>100 cpl) | 10,139 rows (1.15%) - traced to low-observation-count stations, see feature-engineering.md §7 |
| Station/fuel_type pairs with <14 days history | 603 |
| Leakage self-check | `feature_methods_use_only_preceding_or_lag = true`, 0 violations |
| Python/SQL jump-threshold cross-check | Agreed exactly at all 5 candidate thresholds (3/5/7/10/15 cpl) |

## Freshness Monitoring

| Source | Max Stale (hours) | Alert Threshold |
|--------|-------------------|------------------|
| Fuel Prices | 36 | 24 |
| Terminal Gate Prices | 72 | 48 |
| Public Holidays | 2160 (90 days) | 1440 (60 days) |

## Quality Tables

- `silver_data_quality_issues`: Every detected issue with record identifier
- `monitoring_data_quality_results`: Aggregate metrics per rule per run
- `monitoring_source_freshness`: Staleness tracking
