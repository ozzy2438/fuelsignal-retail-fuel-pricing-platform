# FuelSignal — Retail Fuel Pricing & Price-Cycle Forecasting Platform

[![CI](https://github.com/ozzy2438/fuelsignal-retail-fuel-pricing-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/ozzy2438/fuelsignal-retail-fuel-pricing-platform/actions/workflows/ci.yml)

## 🎯 Commercial Problem

Australian retail fuel markets exhibit predictable price cycles — prices jump sharply and then decline gradually over days. The commercial challenge is **timing retail repricing decisions** to balance:
- **Margin preservation** — avoiding being caught below cost during jumps
- **Competitive positioning** — not losing volume by pricing too high during troughs
- **48-hour forecast window** — anticipating when the next market jump will occur

This platform supports daily pricing decisions by:
1. Estimating the probability of a market price jump within the next 48 hours
2. Forecasting competitor price positioning
3. Calculating indicative gross margin (retail price minus terminal gate price)
4. Producing pricing recommendations: **HOLD / FOLLOW / LEAD**
5. Supporting a six-month walk-forward backtest

## 📌 Public Portfolio Framing

> **This is explicitly a public portfolio project using real public data.**
>
> - It is NOT a real client implementation
> - No business results, model accuracy, or revenue uplift are claimed
> - No production adoption has occurred
> - The final pricing decision is designed to remain **human-in-the-loop**
> - Volume and sales data are not available in public sources
> - Any later volume impact will be represented only as a clearly labelled proxy
> - No model-performance or commercial-impact number will be written before it is produced by the backtest

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    DATA SOURCES                                      │
│  NSW FuelCheck │ AIP Terminal Gate Prices │ NSW Public Holidays       │
└─────────────────┴──────────────────────────┴───────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  BRONZE — Raw Immutable Ingestion                                     │
│  • Source metadata  • Ingestion audit  • Record hashing              │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SILVER — Cleaned & Conformed                                        │
│  • Type standardisation  • Fuel-name mapping  • Quality checks         │
│  • Station master  • Competitor pairs (~5km)  • Deduplication          │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  GOLD — Model-Ready Analytics (SQL Window Functions)                  │
│  • Market cycle features  • Competitor positioning                    │
│  • Indicative margin  • Daily pricing inputs                          │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FUTURE: MLflow + Databricks Jobs + Power BI                         │
│  • Price-jump probability model  • Walk-forward backtest              │
│  • HOLD/FOLLOW/LEAD recommendations  • Scheduled retraining           │
└──────────────────────────────────────────────────────────────────────┘
```

## 📂 Data Sources

| Source | Provider | Type | Usage |
|--------|----------|------|-------|
| [NSW FuelCheck](https://data.nsw.gov.au/data/dataset/fuel-check) | NSW Government | Station-level retail prices, 18-month bulk archive (Jan 2025 - Jun 2026) | Primary pricing data |
| [NSW FuelCheck Live Reference API](https://api.nsw.gov.au/Product/Index/22) | NSW Government (OneGov) | Official station code, brand, address, coordinates (live snapshot) | Station coordinates for the bulk archive above, which never carries them |
| [AIP Terminal Gate Prices](https://www.aip.com.au/pricing/terminal-gate-prices) | Australian Institute of Petroleum | Wholesale prices, daily back to 2004 | Margin calculation |
| [ACCC Petrol Price Cycles](https://www.accc.gov.au/consumers/petrol-and-fuel/petrol-price-cycles) | ACCC | Methodology reference | Cycle understanding |
| [NSW Public Holidays](https://www.industrialrelations.nsw.gov.au/public-holidays/public-holidays-in-nsw) | NSW Government | Holiday calendar | Feature engineering |

See [docs/data-sources.md](docs/data-sources.md) for the confirmed live OAuth2 flow, exact
endpoint paths, and known pitfalls for the FuelCheck sources.

## 📁 Repository Structure

```
fuelsignal-retail-fuel-pricing-platform/
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── pyproject.toml
├── requirements.txt
├── Makefile
├── config/
│   ├── project.yml          # Global settings
│   ├── sources.yml          # Data source registry
│   ├── environments.yml     # Dev/staging/prod config
│   └── data_quality.yml     # Quality rules & thresholds
├── src/fuelsignal/
│   ├── __init__.py
│   ├── config.py            # Configuration management
│   ├── logging.py           # Structured logging
│   ├── ingestion/           # Source downloaders
│   ├── bronze/              # Bronze layer schemas
│   ├── silver/              # Silver layer schemas
│   ├── gold/                # Gold layer schemas
│   ├── quality/             # Data quality framework
│   ├── features/            # Feature engineering
│   └── utils/               # Utilities (geo, hashing, validation)
├── notebooks/
│   ├── 00_environment_validation.py
│   ├── 01_bronze_ingestion.py
│   ├── 02_silver_cleaning.py
│   ├── 03_station_master.py
│   ├── 04_competitor_geospatial.py
│   ├── 05_gold_cycle_features.sql
│   └── 06_pipeline_validation.py
├── sql/
│   ├── ddl/                 # Table definitions
│   ├── silver/              # Silver transformations
│   └── gold/                # Gold transformations
├── tests/
│   ├── unit/                # Offline unit tests
│   └── integration/         # Databricks integration tests
├── docs/
└── .github/workflows/ci.yml
```

## 🚀 Local Setup

```bash
# Clone
git clone https://github.com/ozzy2438/fuelsignal-retail-fuel-pricing-platform.git
cd fuelsignal-retail-fuel-pricing-platform

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install
pip install -e ".[dev]"

# Run tests
make test

# Lint
make lint
```

## ☁️ Databricks Setup

1. Copy `.env.example` to `.env`
2. Set your credentials:
   ```
   DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
   DATABRICKS_TOKEN=your-personal-access-token
   ```
3. Run notebook `00_environment_validation.py` to create schemas and tables
4. Run notebook `01_bronze_ingestion.py` to ingest source data
5. Run notebook `02_silver_cleaning.py` for transformations

## 🔒 Security

- **NEVER** commit `.env` or any file containing tokens
- Credentials are loaded exclusively from environment variables
- The `.gitignore` excludes all credential files
- CI checks for accidentally committed secrets
- Logs redact any sensitive information

## 📊 Current Implementation Status

| Component | Status |
|-----------|--------|
| Repository structure | ✅ Complete |
| Configuration framework | ✅ Complete |
| Data source registry | ✅ Complete |
| Bronze table definitions | ✅ Complete |
| Silver table definitions | ✅ Complete |
| Gold table definitions | ✅ Complete |
| Monitoring table definitions | ✅ Complete |
| Public holidays ingestion | ✅ Complete |
| FuelCheck historical ingestion (18 months) | ✅ Complete |
| FuelCheck live station-reference API (coordinates) | ✅ Complete (OAuth2 client-credentials) |
| AIP TGP ingestion | ✅ Complete (single download already covers 2004-present) |
| Station identity crosswalk (address+postcode) | ✅ Complete - see docs/data-quality.md |
| Silver transformations (fuel prices, station master) | ✅ Complete |
| Gold SQL window functions | ✅ Complete - executed live, see docs/feature-engineering.md |
| Competitor geospatial (5km Haversine) | ✅ Complete |
| Price-jump label definition + sensitivity analysis | ✅ Complete - see docs/jump-label-definition.md |
| Gold-layer leakage controls | ✅ Complete - see docs/validation-methodology.md |
| Model-eligibility filter | ✅ Complete - see docs/model-eligibility.md |
| Rule-based 48h jump baseline | ✅ Complete - see docs/model-results.md |
| LightGBM 48h jump classifier | ✅ Complete (first iteration) - see docs/model-results.md |
| Walk-forward model validation | ✅ Complete (4 expanding-window folds) |
| MLflow experiment tracking | ✅ Complete - `/Shared/fuelsignal-jump-model` on Databricks |
| Per-fuel-type decision threshold calibration | ✅ Complete - see docs/threshold-calibration.md |
| 7-day price-level forecast model | ✅ Complete (first iteration) - see docs/price-forecast.md |
| Data quality framework | ✅ Complete |
| Unit tests | ✅ Complete (160 tests) |
| CI/CD pipeline | ✅ Complete |
| Documentation | ✅ Complete |
| Pricing policy (HOLD/FOLLOW/LEAD) | ❌ Not started |
| Walk-forward backtest of a deployed policy | ❌ Not started |
| Power BI integration | ❌ Not started |

## ⚠️ Limitations

1. **First-iteration model, no pricing policy yet** — LightGBM beats the rule-based
   baseline on PR-AUC in every walk-forward fold, but is not uniformly better (the
   baseline wins on F1 for U91, and in one fold overall) - see docs/model-results.md.
   Per-fuel-type thresholds are now calibrated (docs/threshold-calibration.md), but two
   fuel types (U91, P95) still fall back to the shared 0.5 default because no candidate
   threshold cleared the recall/alert-fatigue/lead-time floors simultaneously - their
   underlying model signal (PR-AUC 0.12-0.13) is the weakest of the six fuel types. No
   commercial-impact claim.
2. **Station coverage is bounded by the live reference API's current snapshot** — a bulk
   station that has closed/rebranded since the reference API was last queried, or whose
   address text formatting doesn't match, will not resolve to a coordinate and is
   quarantined rather than guessed (see docs/data-quality.md for exact rule names and
   counts)
3. **AIP TGP** — Published as HTML/XLSX; extraction requires maintenance when page/workbook structure changes
4. **No volume data** — Public sources don't include sales volume; margin analysis is indicative only
5. **Single state** — Currently NSW only; architecture supports multi-state expansion
6. **Free-tier Databricks** — Some Unity Catalog features may be limited; the SQL warehouse
   returned an unexplained `HTTP 403` mid-run once during a large historical backfill -
   idempotent per-file checksums mean a retry safely resumes rather than reprocessing
   everything

## 📋 Next Milestones

Week 1 (Foundation) is complete: station coordinates resolved, 18-month historical
FuelCheck archive ingested (Jan 2025 - Jun 2026, 1,423,296 bronze rows), station identity
crosswalk built (3098 coordinate-bearing stations), `silver_fuel_prices` populated
(1,197,046 rows, ~84% match rate), 51,579 competitor pairs computed within 5km,
audit/monitoring/DQ tables populated.

Week 2 Phase 1 (Gold feature layer) is complete: all six Gold tables populated live
(`gold_station_daily_market`/`gold_market_cycle_features`/`gold_indicative_margin`/
`gold_daily_pricing_inputs`: 879,486 rows each; `gold_competitor_positioning`:
6,961,790; `gold_price_jump_labels`: 3,536), price-jump threshold empirically chosen
(7.0 cpl, see docs/jump-label-definition.md), leakage controls verified (0 duplicate
keys, no lookahead in any feature column, Python/SQL cross-check agrees at every
candidate threshold). See docs/feature-engineering.md and docs/data-quality.md for the
full live results.

Week 2 Phase 2 (Modelling, first iteration) is complete: a model-eligibility filter
excluded 16.0% of station x fuel_type series before training (docs/model-eligibility.md);
a transparent rule-based baseline and a LightGBM classifier were both evaluated with
4-fold walk-forward (never random) validation across U91/E10/P95/P98/DL/PDL
(LPG/E85/B20 excluded - too little history); LightGBM's PR-AUC beat the baseline's in
every fold; every run tracked in the Databricks-hosted MLflow experiment
`/Shared/fuelsignal-jump-model`. Full results: docs/model-results.md. **No pricing
policy exists yet and no commercial-impact claim is made anywhere.**

Week 2 Phase 3 (Threshold calibration + 7-day forecast) is complete:

- **Per-fuel-type threshold calibration** (Part 1) — a business-oriented selection rule
  (recall floor, alert-fatigue cap, minimum lead time - never max-F1-alone) chose a
  threshold per fuel type from a validation-only grid sweep, honestly separated from
  each walk-forward fold's test period. Four of six fuel types (E10/P98/DL/PDL) got a
  calibrated threshold; U91 and P95 fell back to the shared 0.5 default because no
  candidate cleared all three constraints. Live results, the exact floor/cap values and
  why: docs/threshold-calibration.md. Chosen thresholds versioned in
  `config/model_thresholds.yml`; tracked in `/Shared/fuelsignal-jump-model`.
- **Seven-day market-level price forecast** (Part 2) — LightGBM regressors for the
  1/3/7-day horizons, compared against persistence, a 7-day moving average, and a
  14-day linear trend, all walk-forward validated. LightGBM wins decisively at 3 and 7
  days (roughly 30-70% lower WAPE than the baselines, 0.5-0.76 directional accuracy vs
  the baselines' near-zero at day 7); at day 1, persistence has marginally lower error
  but far worse directional accuracy. Full results: docs/price-forecast.md. Tracked in
  a new experiment, `/Shared/fuelsignal-price-forecast`.

**Still no pricing policy and no commercial-impact claim anywhere in the repository.**
Next:

1. **Pricing policy layer** — HOLD/FOLLOW/LEAD decision rules with a TGP margin
   guardrail, built on top of the jump classifier's calibrated thresholds and the price
   forecast
2. **Walk-forward Backtest of the deployed policy** — 6-month out-of-sample validation
   of the policy itself (methodology documented in docs/validation-methodology.md, not
   yet executed against a policy)
3. **Scheduling** — Databricks Jobs for daily automation
4. **Power BI** — Reporting dashboard connected to Gold layer

## 📄 License

MIT License — see [LICENSE](LICENSE)
