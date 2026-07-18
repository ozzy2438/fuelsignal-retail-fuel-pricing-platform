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
│  MLflow + Databricks Jobs (live, scheduled) + Power BI (docs only)   │
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
| Pricing policy (HOLD/FOLLOW/LEAD) | ✅ Complete - three-way safety gate, see docs/pricing-policy.md |
| Six-month walk-forward policy backtest | ✅ Complete - 398,474 rows, see docs/pricing-policy.md |
| Dashboard-ready output tables/views | ✅ Complete - see docs/pricing-policy.md SS7 |
| Databricks Jobs (daily pipeline + monitoring) | ✅ Deployed, live-validated via Run Now, schedules UNPAUSED - see docs/jobs-and-scheduling.md |
| Data quality framework | ✅ Complete |
| Unit tests | ✅ Complete (183 tests) |
| CI/CD pipeline | ✅ Complete |
| Documentation | ✅ Complete |
| Power BI integration | ⚠️ Connection instructions documented, report not yet built - see docs/power-bi-connection.md |

## ⚠️ Limitations

1. **First-iteration model** — LightGBM beats the rule-based baseline on PR-AUC in
   every walk-forward fold, but is not uniformly better (the baseline wins on F1 for
   U91, and in one fold overall) - see docs/model-results.md. Per-fuel-type thresholds
   are calibrated (docs/threshold-calibration.md), but two fuel types (U91, P95) still
   fall back to the shared 0.5 default because no candidate threshold cleared the
   recall/alert-fatigue/lead-time floors simultaneously - their underlying model
   signal (PR-AUC 0.12-0.13) is the weakest of the six fuel types, so they stay in
   watch-only mode in the pricing policy and never receive a LEAD recommendation.
   No commercial-impact claim.
2. **TGP margin guardrail covers only DL and U91** — TGP (wholesale price) data only
   maps to those two fuel types (established in Phase 1); for E10/P95/P98/PDL, the
   pricing policy's FOLLOW recommendation is explicitly `recommendation_status =
   disabled_unsafe` (never automated) because there is no margin floor to protect it.
   DL is the only fuel type with full automation on both LEAD and FOLLOW. A
   retail-spread-based margin proxy was investigated for the other four fuel types
   and deliberately not activated - no ground truth exists to validate it against
   (docs/margin-proxy-investigation.md). See docs/pricing-policy.md SS4-5 for the full
   detail - this is the single most important caveat before any production use.
3. **FuelCheck station-reference credentials not yet provisioned as a job secret** —
   the scheduled `fuelsignal-daily-pipeline` job authenticates to Databricks itself
   fine (a dedicated job-execution PAT was provisioned as a Databricks secret and
   live-validated via Run Now), but `FUELCHECK_API_KEY`/`FUELCHECK_API_SECRET` were
   not, so the ingestion task's station-reference refresh sub-step fails gracefully
   rather than running - Bronze/Silver/Gold refresh and jump/forecast scoring are
   unaffected (see docs/jobs-and-scheduling.md §6).
4. **Station coverage is bounded by the live reference API's current snapshot** — a bulk
   station that has closed/rebranded since the reference API was last queried, or whose
   address text formatting doesn't match, will not resolve to a coordinate and is
   quarantined rather than guessed (see docs/data-quality.md for exact rule names and
   counts)
5. **AIP TGP** — Published as HTML/XLSX; extraction requires maintenance when page/workbook structure changes
6. **No volume data** — Public sources don't include sales volume; margin analysis is indicative only
7. **Single state** — Currently NSW only; architecture supports multi-state expansion
8. **Free-tier Databricks** — Some Unity Catalog features may be limited; the SQL warehouse
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

**Still no commercial-impact claim anywhere in the repository.**

Week 2 Phase 4 (Pricing policy + six-month backtest) is complete: HOLD/FOLLOW/LEAD
rules (`src/fuelsignal/policy/pricing_policy.py`) combine the calibrated jump
probability, the 3-day/7-day forecast, competitor positioning, and a TGP margin
guardrail - jump-model automation (and therefore any LEAD) is enabled only for
E10/P98/DL/PDL, with U91/P95 in conservative watch-only mode per Phase 3's finding
that their threshold never cleared the business-rule constraints. Backtested
leakage-safe over the entire 186-day out-of-sample span after the (unretrained) Phase
2 classifier's train cutoff (2025-12-27 -> 2026-06-30): 398,474 recommendations
written to `monitoring_pricing_policy_recommendations`, aggregated per fuel type in
`monitoring_policy_backtest_summary`. Key finding: **the TGP margin guardrail only
has data to act on for DL and U91** (TGP is null for the other four fuel types in
this window) - the single most important caveat before any production use. LEAD
recommendations were followed by an actual jump at 1.7-2.9x the base rate across
every automated fuel type. Full results: docs/pricing-policy.md. Tracked in
`/Shared/fuelsignal-pricing-policy`. Config: `config/pricing_policy.yml`.

Week 2 Phase 5 (Operationalisation) is complete: a three-way `recommendation_status`
safety gate (`automated`/`watch_only`/`disabled_unsafe`) now sits alongside `action`
on every recommendation - full automation requires *both* jump-model eligibility
*and* a validated TGP margin guardrail, so E10/P98/PDL keep automated LEAD but their
FOLLOW is `disabled_unsafe`, P95 is `disabled_unsafe` on both counts, and **DL is the
only fuel type with full automation**. The margin guardrail floor was re-tuned from
1.0 to 2.0 cpl (a grid sweep of the existing backtest data showed this improves DL's
average margin difference by 0.45 cpl and U91's by 0.22 cpl for a negligible staleness
cost). A retail-spread-based margin proxy for the four uncovered fuel types was
investigated and explicitly **not activated** - no ground truth TGP data exists to
validate it against (`docs/margin-proxy-investigation.md`). Four dashboard-ready views
plus a `monitoring_fuel_policy_status` reference table were deployed
(`docs/pricing-policy.md` §7). Two Databricks Jobs (`fuelsignal-daily-pipeline`,
`fuelsignal-monitoring-checks`) were deployed live with real cron schedules, initially
left **PAUSED** pending credential provisioning. Full results: docs/pricing-policy.md.

Week 2 Phase 6 (Final operational validation) is complete: a dedicated job-execution
PAT was provisioned and stored as a Databricks secret (never committed to git);
`scripts/score_daily.py` now reads only a bounded 60-day trailing window instead of
the full historical archive (127,171 rows pulled instead of 839,906, same 1,739 rows
scored); both jobs were run end-to-end via Run Now and validated (auth succeeded, all
tasks `SUCCESS`, `monitoring_pricing_policy_recommendations`/`monitoring_pipeline_runs`
updated with **zero duplicate rows**, run metadata logged to both the Databricks job
history and MLflow) before their schedules were switched to **UNPAUSED**. Seven
distinct live-only infrastructure bugs were found and fixed in the process (serverless
environment client version, git-sourced file tasks needing explicit `source: GIT`,
`__file__`/cwd resolution under Databricks' exec-style execution,
`spark_env_vars` not reaching the task process, `{{secrets/...}}` templating not
resolving in task parameters, `SystemExit(0)` being treated as task failure, and an
MLflow query picking up the wrong run) - full list: docs/jobs-and-scheduling.md §4.
Exact Power BI connection instructions for the four dashboard-ready objects were
documented: docs/power-bi-connection.md. The three-way `recommendation_status` safety
gate was not touched during this phase - no unsafe fuel policy was enabled.

**Still no commercial-impact claim anywhere in the repository.** Next:

1. **Provision FuelCheck station-reference credentials as a job secret** — the one
   remaining ingestion sub-step not yet wired up for the scheduled job (see the
   limitations section above and docs/jobs-and-scheduling.md §6)
2. **Resolve the TGP coverage gap** — either accept it as a permanent limitation for
   E10/P95/P98/PDL or source real wholesale data for those four fuel types before any
   production FOLLOW automation (docs/margin-proxy-investigation.md)
3. **Power BI** — build the actual report against the documented connection
   (docs/power-bi-connection.md); the dashboard views/tables themselves are live and
   already validated end to end

## 📄 License

MIT License — see [LICENSE](LICENSE)
