# FuelSignal Architecture

## Overview

FuelSignal is built on the **Databricks Lakehouse** architecture using a Medallion (Bronze/Silver/Gold) data model with Delta Lake storage.

## System Architecture

### Data Flow

```
External Sources          Databricks Lakehouse              Consumption
──────────────────     ──────────────────────────────    ───────────

NSW FuelCheck ──────▶ Bronze (Raw) ──▶ Silver (Clean) ──▶ Gold (Analytics)
   (CKAN API)         │                  │                    │
AIP TGP ───────────▶┤  Immutable       │  Validated         │  Model-ready
   (HTML)             │  + Metadata      │  + Normalized      │  + Features
Public Holidays ────▶┤                  │  + Station Master  │
   (API/Static)       │                  │  + Competitor Sets  ├─▶ MLflow
                      │                  │                    ├─▶ Power BI
                      └── Audit Trail    └── DQ Issues        └─▶ Pricing API
```

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|----------|
| Storage | Delta Lake | ACID transactions, time travel, schema enforcement |
| Compute | Databricks (PySpark + SQL) | Distributed processing |
| Orchestration | Databricks Notebooks / Jobs | Pipeline scheduling |
| ML | MLflow (planned) | Experiment tracking, model registry |
| Reporting | Power BI (planned) | Business dashboards |
| CI/CD | GitHub Actions | Automated testing and validation |
| Config | YAML | Environment and source configuration |

### Medallion Layers

#### Bronze (Raw)
- Immutable source data preservation
- Ingestion metadata on every record
- Audit trail for every pipeline run
- No transformation — store exactly what was received

#### Silver (Conformed)
- Type standardisation and casting
- Fuel type normalization across sources
- Deterministic station identity crosswalk: the bulk price archive (no station code, no
  coordinates) is matched against the live FuelCheck reference API (official code +
  coordinates, no price history) via a normalized `address + postcode` key - see
  `src/fuelsignal/silver/station_matching.py` and `data-quality.md`
- Coordinate validation (NSW bounds)
- Price plausibility checks
- Duplicate detection and handling
- Competitor pair computation (Haversine with a bounding-box pre-filter, 5km radius,
  single direction per pair since the relationship is symmetric)
- Quality issues recorded (never silently dropped) - ambiguous or unmatched stations are
  quarantined, never guessed

#### Gold (Analytics)
- SQL window functions for rolling aggregates
- Days-since-last-jump event tracking
- Market percentile positioning
- Indicative margin (retail - TGP)
- Combined model-ready feature table
- Calendar features (day of week, public holidays)

### Security Architecture

- Credentials: Environment variables only (never in code/config)
- Logging: Automatic redaction of sensitive values
- CI: Secret scanning on every push
- Git: `.gitignore` blocks all credential files
- Validation: Connection tested without displaying tokens

### Data Quality Architecture

- Rule-based checks defined in `config/data_quality.yml`
- Reusable check functions in `src/fuelsignal/quality/`
- Invalid records written to `silver_data_quality_issues`
- Per-run summary in `monitoring_data_quality_results`
- Source freshness monitoring
- Configurable severity levels and actions
