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
| station_id not null | Critical | Quarantine |
| price is numeric | Critical | Quarantine |
| price between [80, 300] cpl | Warning | Flag |
| timestamp parseable | Critical | Quarantine |
| latitude in [-37.5, -28.0] | Warning | Flag |
| longitude in [141.0, 154.0] | Warning | Flag |
| no duplicate (station, fuel, time) | Warning | Deduplicate |

### Terminal Gate Prices
| Rule | Severity | Action |
|------|----------|--------|
| date parseable | Critical | Quarantine |
| terminal identifiable | Critical | Quarantine |
| TGP positive | Critical | Quarantine |
| TGP between [60, 250] cpl | Warning | Flag |
| no duplicate (date, terminal, fuel) | Warning | Deduplicate |

### Station Master
| Rule | Severity | Action |
|------|----------|--------|
| station_id not null | Critical | Quarantine |
| valid coordinates | Warning | Flag |
| brand normalized | Info | Log |
| duplicate station detection | Warning | Flag |

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
