# FuelSignal Model Results (Week 2 Phase 2, first iteration)

Trained and evaluated live 2026-07-18 against the Databricks workspace by
`scripts/train_jump_model.py`. All experiments tracked in the Databricks-hosted MLflow
experiment `/Shared/fuelsignal-jump-model`.

> **Scope boundary**: this document reports model performance only. No pricing policy
> exists yet, and no commercial-impact, revenue, or margin-uplift number is claimed
> anywhere here or elsewhere in the repository - see `assumptions-and-limitations.md`.

## 1. Scope of this iteration

- **Fuel types**: U91, E10, P95, P98, DL, PDL only. LPG, E85, and B20 are deliberately
  excluded - their raw Silver history (2,363 / 401 / 8 rows respectively) is too thin
  for a stable jump-label frequency (see `jump-label-definition.md`).
- **Target**: `jump_within_48h` from `gold_price_jump_labels` (market-level, i.e. the
  same label value for every station of a given fuel_type on a given date).
- **Eligibility filter applied first**: only rows from series passing
  `model-eligibility.md`'s criteria are used (839,906 of 876,944 target-fuel rows).
- **Features**: the 25 leakage-safe columns of `gold_daily_pricing_inputs` (see
  `feature-engineering.md`) plus `fuel_type` as a categorical feature. `station_id` is
  deliberately excluded as a feature (too high-cardinality - 1,905 distinct stations
  in the training pull - to use directly without memorization risk in a first
  iteration).

## 2. Validation methodology

Walk-forward (expanding-window), never random - see `validation-methodology.md`.
`config/project.yml -> modelling`: `walk_forward_folds: 4`, `min_train_days: 180`,
`test_days: 60`. Train always starts at the series' first date and only grows; test
always immediately follows and never overlaps train.

## 3. Two evaluation grains

- **Row grain** (station x fuel_type x date, the model's actual prediction unit):
  precision, recall, F1, PR-AUC. Because the target is a market-wide event, many rows
  on the same date share the same label - PR-AUC at row grain reflects how well
  station-level features help discriminate, not an independent-trials assumption.
- **Market-day grain** (one decision per fuel_type x date, via majority vote across
  that day's station-level predictions): false-alarm count and average warning lead
  time - both are inherently market-wide concepts (a repricing decision is made once
  per market per day).

## 4. Baseline

One parameter, fit on the TRAIN portion of each fold only: the median number of days
between consecutive market-level jumps (`median_inter_jump_gap_days`). Predicts
`jump_within_48h = True` once at least that many days have passed since the last
detected market jump (`baseline_predict`). No learned parameters, fully explainable in
one sentence.

**Known limitation of the baseline's PR-AUC**: the baseline has no continuous
confidence score (its prediction is a hard 0/1 rule), so PR-AUC is computed against a
degenerate two-value score and should be read alongside precision/recall/F1, not
in isolation.

## 5. Results by fuel type and fold

### 5a. Row-grain, all fuel types combined, per fold

| Fold | Test period | LightGBM P / R / F1 / PR-AUC | Baseline P / R / F1 / PR-AUC | Actual positive rate |
|---|---|---|---|---|
| 0 | 2025-06-30 → 2025-08-28 | 0.167 / 0.397 / 0.235 / **0.141** | 0.052 / 0.547 / 0.096 / 0.055 | 5.8% |
| 1 | 2025-08-29 → 2025-10-27 | 0.355 / 0.220 / 0.271 / **0.325** | 0.261 / 0.441 / **0.328** / 0.259 | 25.7% |
| 2 | 2025-10-28 → 2025-12-26 | 0.256 / 0.449 / **0.326** / **0.262** | 0.186 / 0.415 / 0.257 / 0.164 | 14.8% |
| 3 | 2025-12-27 → 2026-02-24 | 0.104 / 0.453 / **0.170** / **0.166** | 0.028 / 0.365 / 0.052 / 0.045 | 5.6% |

**LightGBM's PR-AUC beats the baseline's in all four folds** - the consistent
signal in this first iteration. F1 is mixed: LightGBM wins 3 of 4 folds, but fold 1
is a case where the baseline's much higher recall (0.441 vs 0.220) gives it the
better F1 despite LightGBM's better PR-AUC (0.325 vs 0.259) - i.e. LightGBM ranks
days better but its default 0.5 probability threshold is not well calibrated for
every fold.

### 5b. Row-grain, by fuel type, averaged across all 4 folds

| Fuel | LightGBM P / R / F1 / PR-AUC | Baseline P / R / F1 / PR-AUC | Winner (F1) | Winner (PR-AUC) |
|---|---|---|---|---|
| U91 | 0.060 / 0.064 / 0.054 / 0.152 | 0.169 / 0.570 / **0.214** / 0.141 | Baseline | LightGBM |
| E10 | 0.203 / 0.466 / **0.220** / **0.213** | 0.156 / 0.581 / 0.180 / 0.140 | LightGBM | LightGBM |
| P95 | 0.188 / 0.356 / **0.211** / **0.183** | 0.095 / 0.785 / 0.155 / 0.105 | LightGBM | LightGBM |
| P98 | 0.269 / 0.535 / **0.328** / **0.263** | 0.132 / 0.373 / 0.175 / 0.132 | LightGBM | LightGBM |
| DL | 0.237 / 0.305 / **0.259** / **0.227** | 0.129 / 0.416 / 0.177 / 0.137 | LightGBM | LightGBM |
| PDL | 0.272 / 0.443 / **0.323** / **0.366** | 0.274 / 0.306 / 0.269 / 0.219 | LightGBM | LightGBM |

**U91 is the one fuel type where the baseline wins on F1**: LightGBM's recall
collapses to 0.064 (vs the baseline's 0.570). Since one joint model was trained
across all six fuel types at a single default 0.5 probability threshold (per
`model-results.md` §1's scope), U91's specific class balance is not well served by
that shared threshold - a candidate fix for the next iteration is per-fuel-type
threshold calibration, not attempted here (out of scope: "do not begin the pricing
policy" - threshold choice is a policy-adjacent decision).

### 5c. Market-day grain: warnings, false alarms, and lead time (summed/averaged across all 4 folds)

| Fuel | LightGBM warnings / false alarms / avg lead (days) | Baseline warnings / false alarms / avg lead (days) |
|---|---|---|
| U91 | 15 / 13 / 2.00 | 111 / 97 / 1.50 |
| E10 | 47 / 33 / 1.19 | 119 / 105 / 1.50 |
| P95 | 67 / 55 / 1.57 | 163 / 144 / 1.48 |
| P98 | 58 / 39 / 1.46 | 108 / 96 / 1.50 |
| DL | 48 / 31 / 1.42 | 65 / 53 / 1.50 |
| PDL | 91 / 60 / 1.46 | 48 / 32 / 1.44 |

LightGBM issues far fewer total warnings than the baseline for every fuel type
except PDL (e.g. U91: 15 vs 111) while keeping a comparable false-alarm *rate*
(roughly 60-87% for both models depending on fuel type) and a similar average lead
time (~1.2-2.0 days, consistent with the max_lead_days=2 window). In practice this
means LightGBM would trigger far less "alert fatigue" for the same rough
false-alarm rate - a meaningfully different operational profile from the baseline,
even where their row-grain F1 scores are close.

## 6. MLflow tracking

Experiment: `/Shared/fuelsignal-jump-model` on the connected Databricks workspace
(experiment ID `1147111250617792`). One parent run per script execution (params: fuel
types, fold config, code version, `eligible_rows=839906`); one nested run per fold
(params: fold date boundaries, per-fuel-type baseline cycle length; ~156-158 metrics
each: `{model}_{fuel_type}_{metric}` and `{model}_{fuel_type}_market_{metric}` for
both `baseline` and `lightgbm`). The final fold's trained LightGBM model is logged
once, on the parent run, as an MLflow 3.x Logged Model (not once per fold - see
implementation note below).

**Implementation notes from the live run (2026-07-18):**
- Individual `mlflow.log_metric()` calls (one REST round-trip each) made the pipeline
  take upwards of an hour per fold in this network environment; refactored to batch
  every fold's ~156 metrics into one `mlflow.log_metrics(dict)` call, cutting each
  fold from many minutes to under a minute.
- Logging a full LightGBM model artifact once per fold (4 uploads) caused one run to
  hang indefinitely partway through fold 2's upload; refactored to log only the final
  (most recent walk-forward) fold's model once, on the parent run - fold-level metrics
  already capture every fold's performance without needing every fold's model
  artifact preserved.
- The short-lived Databricks CLI OAuth token used for local development expired
  during one full run, failing only the final "close run" API call after all folds'
  data had already been safely logged - recovered by re-authenticating and calling
  `mlflow.tracking.MlflowClient.set_terminated` directly rather than re-running the
  full pipeline.

## 7. What this iteration does NOT include

- No pricing policy (hold/follow/lead) - Week 2 Phase 3.
- No walk-forward backtest of a deployed decision rule - only model validation.
- No commercial-impact, margin-uplift, or revenue claim.
- No per-fuel-type probability threshold calibration (all predictions use the default
  0.5 cutoff) - a natural next step once a policy layer defines what threshold
  actually matters commercially.
- No 7-day price-level forecast model yet (separate deliverable, not started).
