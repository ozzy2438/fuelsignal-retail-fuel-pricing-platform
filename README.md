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
| [NSW FuelCheck](https://data.nsw.gov.au/data/dataset/fuel-check) | NSW Government | Station-level retail prices | Primary pricing data |
| [AIP Terminal Gate Prices](https://www.aip.com.au/pricing/terminal-gate-prices) | Australian Institute of Petroleum | Wholesale prices | Margin calculation |
| [ACCC Petrol Price Cycles](https://www.accc.gov.au/consumers/petrol-and-fuel/petrol-price-cycles) | ACCC | Methodology reference | Cycle understanding |
| [NSW Public Holidays](https://www.industrialrelations.nsw.gov.au/public-holidays/public-holidays-in-nsw) | NSW Government | Holiday calendar | Feature engineering |

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
| FuelCheck ingestion | ⚠️ Partial (API registration may be needed) |
| AIP TGP ingestion | ⚠️ Partial (HTML parsing refinement needed) |
| Silver transformations | ✅ Complete |
| Gold SQL window functions | ✅ Complete |
| Competitor geospatial | ✅ Complete |
| Data quality framework | ✅ Complete |
| Unit tests | ✅ Complete |
| CI/CD pipeline | ✅ Complete |
| Documentation | ✅ Complete |
| ML models | ❌ Not started (requires data foundation) |
| Walk-forward backtest | ❌ Not started |
| Power BI integration | ❌ Not started |

## ⚠️ Limitations

1. **No ML models yet** — The data foundation must be validated before modelling
2. **FuelCheck API** — May require registration for real-time access; historical bulk downloads via CKAN
3. **AIP TGP** — Published as HTML tables; extraction requires maintenance when page structure changes
4. **No volume data** — Public sources don't include sales volume; margin analysis is indicative only
5. **Single state** — Currently NSW only; architecture supports multi-state expansion
6. **Free-tier Databricks** — Some Unity Catalog features may be limited

## 📋 Next Milestones

1. **Data Population** — Complete FuelCheck historical data download and Bronze loading
2. **Silver Validation** — Full pipeline run with quality metrics
3. **Gold Materialization** — Execute window function queries on real data
4. **Feature Store** — Register features for ML consumption
5. **ML Model v1** — Binary classifier for 48-hour jump probability
6. **Walk-forward Backtest** — 6-month out-of-sample validation
7. **Recommendation Engine** — HOLD/FOLLOW/LEAD decision layer
8. **Scheduling** — Databricks Jobs for daily automation
9. **Power BI** — Reporting dashboard connected to Gold layer

## 📄 License

MIT License — see [LICENSE](LICENSE)
