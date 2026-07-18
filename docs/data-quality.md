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
