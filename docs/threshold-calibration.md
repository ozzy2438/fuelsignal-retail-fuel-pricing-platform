# Threshold Calibration (Week 2 Phase 3, Part 1)

Calibrated and evaluated live 2026-07-18 against the Databricks workspace by
`scripts/calibrate_thresholds.py`. Tracked in the same Databricks-hosted MLflow
experiment as the jump model, `/Shared/fuelsignal-jump-model`, run ID
`09aaf096a530448880af487ee2fc6d8b`. Chosen thresholds, their validation metrics, and
their rationale are versioned in `config/model_thresholds.yml`.

> **Scope boundary**: this document reports decision-threshold performance only. No
> pricing policy exists yet, and no commercial-impact, revenue, or margin-uplift number
> is claimed here - see `assumptions-and-limitations.md`.

## 1. Why the shared 0.5 threshold needed calibrating

`model-results.md` §5b flagged that a single joint LightGBM model evaluated at the
default 0.5 probability cutoff serves fuel types very unevenly - U91's recall
collapsed to 0.064 there, versus P98's much healthier 0.535. A shared threshold cannot
be right for every fuel type when their class balance and PR-AUC differ this much; this
phase calibrates one threshold per fuel type instead.

## 2. Split methodology: fit-train / validation / test, honestly separated

Each of the four walk-forward folds (`config/project.yml -> modelling`,
`walk_forward_folds: 4`, unchanged from Phase 2) is split three ways:

- **fit-train**: `fold.train_start` → `fold.train_end - validation_days`
- **validation**: the last `validation_days` (30, `config/project.yml ->
  threshold_calibration.validation_days`) of `fold.train`
- **test**: `fold.test_start` → `fold.test_end`, exactly as in Phase 2 - untouched by
  calibration

Two LightGBM models are trained per fold: one on fit-train only (used to score the
validation slice for threshold selection) and one on the full fold.train (used to score
the test slice, matching Phase 2's methodology exactly so test-period numbers stay
comparable). **The test period is never used to choose a threshold** - only to report
final performance after the choice is locked in. Live pooled sizes: 109,261 validation
rows and 325,645 test rows across the 4 folds.

## 3. Threshold grid and business-oriented selection rule

Grid: 0.05 to 0.95 in steps of 0.05 (19 candidates), swept independently per fuel type
on pooled validation data. For each candidate: precision, recall, F1, false-positive
rate, alerts, false alarms per market-month, and average warning lead time (market-day
grain, `src/fuelsignal/modelling/evaluation.py`, `max_lead_days=2` as in Phase 2).
PR-AUC is computed once per fuel type (threshold-independent).

**The rule never picks by maximum F1 alone** (`select_threshold`,
`src/fuelsignal/modelling/threshold_selection.py`, 6 unit tests). A candidate must
clear three floors/caps *simultaneously* before F1 is used as a tiebreaker:

| Constraint | Value | Why |
|---|---|---|
| `min_recall` | 0.30 | A threshold that misses more than 70% of real jumps is not a usable warning signal, regardless of how clean its alerts are. |
| `max_false_alarms_per_market_month` | 6.0 | Roughly one false alarm every five days at most - beyond that, a daily-reviewed feed becomes noise its recipients learn to ignore (the alert-fatigue failure mode). Chosen against Phase 2's observed range: the baseline was producing double-digit false alarms/month for several fuel types (`model-results.md` §5c), while LightGBM was already closer to single digits - 6.0 sits as a meaningful tightening of the baseline's operating point without demanding an unrealistically clean signal from a first-iteration model. |
| `min_avg_lead_time_days` | 1.0 | A warning issued the same day the jump already happened carries no decision-making value; at least one day of lead time is the floor for the signal to be actionable ahead of the event. A threshold with *no* matched warnings at all (lead time undefined) is rejected here, not silently treated as acceptable. |

If no candidate in the grid clears all three, the threshold **falls back to the
previous shared default (0.5)** rather than picking an arbitrary or disqualified
threshold - this happened live for two fuel types (see §5).

## 4. Comparison: shared 0.5 vs rule-based baseline vs calibrated, on untouched test data

All three approaches evaluated on the same pooled test rows (row grain, never seen
during either training or threshold selection):

| Fuel | Calibrated threshold | Calibrated P/R/F1 | Shared 0.5 P/R/F1 | Rule-based baseline P/R/F1 |
|---|---|---|---|---|
| U91 | 0.50 (fallback) | 0.136 / 0.104 / 0.118 | 0.136 / 0.104 / 0.118 | 0.093 / 0.493 / 0.157 |
| E10 | 0.40 | 0.174 / 0.467 / **0.253** | 0.192 / 0.375 / 0.254 | 0.079 / 0.403 / 0.133 |
| P95 | 0.50 (fallback) | 0.127 / 0.390 / 0.192 | 0.127 / 0.390 / 0.192 | 0.078 / 0.712 / 0.140 |
| P98 | 0.35 | 0.209 / 0.656 / 0.317 | 0.243 / 0.509 / **0.329** | 0.082 / 0.393 / 0.136 |
| DL | 0.35 | 0.211 / 0.394 / **0.275** | 0.254 / 0.301 / 0.276 | 0.165 / 0.358 / 0.225 |
| PDL | 0.65 | 0.351 / 0.356 / **0.354** | 0.260 / 0.445 / 0.329 | 0.258 / 0.280 / 0.268 |

**Calibration is not a uniform win on test-period F1** - it beats the shared 0.5
default outright only for PDL, and is essentially tied for E10/DL. This is expected and
honest: the rule optimizes for the business constraints in §3 (recall floor,
alert-fatigue cap, lead-time floor) on *validation* data, not for test-period F1 - a
validation-optimal operating point does not automatically dominate every metric on an
unseen period, especially with only 30 days of validation data per fold. What
calibration reliably does versus the *rule-based baseline* is far more consistent: the
calibrated/default LightGBM thresholds beat the baseline's F1 in every fuel type except
U91 and P95 (see §5), often by a wide margin (E10: 0.253 vs 0.133; P98: 0.317 vs 0.136).

## 5. Two fuel types where no threshold qualified: U91 and P95

For **U91** and **P95**, no point in the 0.05-0.95 grid satisfied all three
constraints simultaneously, so both fall back to the shared 0.5 default
(`fallback_to_0.5: true` in `config/model_thresholds.yml`). Checking what the grid's
*pure max-F1* point looks like for these two explains why:

| Fuel | Max-F1 threshold | F1 at that point | Recall | False alarms/market-month |
|---|---|---|---|---|
| U91 | 0.05 | 0.213 | 0.512 | 11.47 (over the 6.0 cap) |
| P95 | 0.05 | 0.228 | 0.869 | 21.62 (over the 6.0 cap) |

Every threshold in the grid for these two fuel types trades recall against alert
fatigue in a way that never clears both floors at once - lowering the threshold enough
to hit `min_recall=0.30` always pushes false alarms well past `max_false_alarms_per_market_month=6.0`.
This matches Phase 2's finding that U91 has the model's weakest signal
(PR-AUC 0.119 here; P95 is similarly weak at 0.127, both well below DL's 0.321 and
PDL's 0.386) - the business rule is correctly refusing to manufacture a confident
threshold out of a genuinely weak ranking, rather than reporting a threshold that would
look fine on paper (by F1) but fail operationally (by alert volume). This is a concrete
example of requirement #4 in action, not a corner case to explain away.

## 6. Chosen thresholds and validation metrics (live results)

| Fuel | Threshold | Selection | Validation P/R/F1 | False alarms/mo | Avg lead (days) | PR-AUC (validation) |
|---|---|---|---|---|---|---|
| U91 | 0.50 | fallback (no candidate qualified) | - | - | - | 0.119 |
| E10 | 0.40 | highest F1 among qualifying | 0.182 / 0.306 / 0.228 | 5.74 | 1.40 | 0.229 |
| P95 | 0.50 | fallback (no candidate qualified) | - | - | - | 0.127 |
| P98 | 0.35 | highest F1 among qualifying | 0.226 / 0.453 / 0.302 | 5.29 | 1.50 | 0.191 |
| DL | 0.35 | highest F1 among qualifying | 0.365 / 0.322 / 0.342 | 3.97 | 1.44 | 0.321 |
| PDL | 0.65 | highest F1 among qualifying | 0.437 / 0.431 / 0.434 | 3.09 | 1.60 | 0.386 |

The full grid (all 19 candidates x 6 fuel types, `fuel_results[fuel]["grid"]`) is
logged as the MLflow artifact `threshold_calibration_results.json` on the run above -
not duplicated into this document or into `config/model_thresholds.yml`, which only
carries the chosen threshold and its validation metrics per fuel type.

## 7. What this phase does NOT include

- No pricing policy (hold/follow/lead) or backtest of one - still Week 2 Phase 3's Part
  1 scope only.
- No re-training of the underlying LightGBM classifier - thresholds are calibrated
  against Phase 2's existing model architecture and features, unchanged.
- No commercial-impact, revenue, or margin-uplift claim.
- U91 and P95 remain on the shared 0.5 default because no better threshold could be
  defended under the business constraints - a candidate for future work is improving
  those two fuel types' underlying model signal (better features or more data), not
  relaxing the constraints to force a threshold choice.
