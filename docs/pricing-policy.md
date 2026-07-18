# Pricing Policy and Six-Month Backtest (Week 2 Phase 4)

Executed live 2026-07-18 against the Databricks workspace by
`scripts/run_pricing_policy_backtest.py`. Tracked in a new MLflow experiment,
`/Shared/fuelsignal-pricing-policy`, run ID `9a660be77e5f4e24a0749e270bc5b3d8`.
Row-level recommendations are in `fuelsignal.fuelsignal_monitoring.monitoring_pricing_policy_recommendations`
(398,474 rows); the aggregate comparison against an always-HOLD baseline is in
`fuelsignal.fuelsignal_monitoring.monitoring_policy_backtest_summary` (7 rows: one
per fuel type plus one `ALL` row). Full numbers are also in
`config/pricing_policy_backtest_results.json`.

> **Scope boundary**: this document reports backtest mechanics and indicative margin
> only. No revenue, profit, or realised P&L claim is made anywhere here - sales-volume
> data is unavailable, so every margin number is `retail_price - TGP`, never a P&L
> figure. See `assumptions-and-limitations.md`.

## 1. What was reused, and what had to be fit once

Per the task's explicit instruction, nothing was retrained. Specifically:

- **Jump classifier**: the exact LightGBM model Phase 2 already trained and logged
  (`models:/m-8afad3aa473649f3b4de14a939de11f3`, from MLflow run
  `b3e80d2cea3544768901c260b48188cf`, trained on 2025-01-01..2025-12-26) is looked up
  live by tag/name (`find_phase2_jump_model` in the backtest script - never a
  hardcoded ID) and used exactly as-is to score every backtest row's jump
  probability.
- **Calibrated thresholds**: `config/model_thresholds.yml` from Phase 3, unchanged.
- **Price forecast**: Phase 3 evaluated the 3-day/7-day forecast's *accuracy* but
  never persisted a deployable model - there was nothing to reuse. This script fits
  exactly one 3-day and one 7-day LightGBM regressor, using
  `scripts/forecast_prices.py`'s identical, unmodified feature set and
  hyperparameters, on the same 2025-01-01..2025-12-26 window as the jump classifier,
  and logs both to MLflow this time. This is a one-time artifact of already-validated
  methodology, not a retrain or redesign - no feature or hyperparameter changed from
  Phase 3.

## 2. Backtest window: the entire leakage-safe span available

`config/pricing_policy.yml`: `backtest_start_date: 2025-12-27`,
`backtest_end_date: 2026-06-30` - 186 days (~6.1 months), chosen as the *entire*
out-of-sample span after the jump classifier's train cutoff through the current end
of the Gold archive. The classifier has seen none of this data. The policy is applied
independently to every eligible station-fuel-day in this window - a leakage-safe,
per-day recommendation generation using only information available that day, not a
compounding price-trajectory simulation (a FOLLOW or LEAD recommendation on one day
does not change the "current price" the policy sees on the next day - that would
require assuming the recommendation was actually adopted, which this backtest does
not claim).

## 3. HOLD / FOLLOW / LEAD rules

Implemented in `src/fuelsignal/policy/pricing_policy.py` (10 unit tests), numeric
thresholds versioned in `config/pricing_policy.yml`. Precedence:

1. **LEAD** - only for automated fuel types (§4), only when the calibrated jump
   probability clears its Phase 3 threshold **and** the 3-day forecast confirms a
   rise of at least `lead_min_forecast_change_cpl` (5.0 cpl - the day-3 forecast's own
   typical MAE from `docs/price-forecast.md`, so a smaller move is not trusted)
   **and** the station is not already priced above its local competitor median
   (leading from an uncompetitive position only makes it worse). The recommended move
   is a modest `lead_step_cpl` (2.0 cpl) anticipatory raise, not the full forecast
   move - real cyclical fuel pricing "leads" with a small test increment.
2. **FOLLOW** - triggered reactively once a station is priced at least
   `follow_min_overpriced_cpl` (4.0 cpl, the live 75th percentile of
   `station_vs_competitor_median_cpl`) above its local competitor median, or
   proactively if the 3-day forecast predicts a decline of at least
   `follow_forecast_decline_cpl` (5.0 cpl, same day-3-MAE rationale as LEAD). The
   **TGP margin guardrail** (§5) can cap how far a FOLLOW is allowed to cut price; if
   the capped price is not actually below the current price, the recommendation
   downgrades to HOLD rather than issuing a FOLLOW that changes nothing.
3. **HOLD** - the default when nothing above triggers, and whenever
   `current_price_cpl` is missing.

## 4. Jump-model automation: four fuel types only

Per the task's explicit instruction, jump-model automation - and therefore any LEAD
recommendation - is enabled **only** for E10, P98, DL, PDL
(`config/pricing_policy.yml -> automated_fuel_types`). U91 and P95 stay in
`watch_only` mode: their calibrated threshold fell back to the shared 0.5 default in
Phase 3 because no candidate cleared the business-rule constraints
(`docs/threshold-calibration.md` SS5) - their PR-AUC (~0.12-0.13) is the weakest of the
six fuel types. `PolicyInputs.automation_enabled=False` makes `jump_expected` always
`False` for these two in `decide_policy`, so **no LEAD is ever issued for U91 or P95**
- confirmed live: `lead_count=0` for both in the backtest results (§6). They still
receive FOLLOW/HOLD recommendations from competitor positioning and the price
forecast, which performs comparably or better for U91/P95 than the other fuel types
(`docs/price-forecast.md`).

## 5. TGP margin guardrail - and its real coverage limit

The guardrail never lets a FOLLOW push the indicative margin (`retail_price - TGP`)
below `min_margin_guardrail_cpl` (1.0 cpl, just above the live 10th percentile of
`gold_indicative_margin.indicative_margin_cpl`, 0.5 cpl).

**This guardrail can only function where TGP data exists.** Verified live for the
backtest window: `tgp_cpl` is **100% non-null for DL and U91, and 100% null for
E10, P95, P98, and PDL** (established in Phase 1 - TGP source data only maps to
those two fuel types). This is not a bug in the policy - `decide_policy` correctly
returns `hypothetical_margin_cpl=None` and never triggers the guardrail when
`tgp_cpl` is `None` - but it is a real, material limitation: **the TGP-based
minimum-margin guardrail is only ever active for 2 of the 6 fuel types.** For the
other four, every FOLLOW that positioning or the forecast triggers executes in full,
with no margin floor at all. This is reported plainly, not smoothed over - see §6 and
§8.

(An earlier version of this backtest's summary aggregation reported `0.0` average/
total margin difference for the four TGP-less fuel types, which looked like "no
margin impact" rather than the true "no margin data." This was caught and corrected
- both in the script and in the already-written `monitoring_policy_backtest_summary`
rows and MLflow artifact - before this document was written; see the
`margin_data_correction` tag on the MLflow run.)

## 6. Six-month backtest results (2025-12-27 -> 2026-06-30)

### 6a. Databricks row counts

| Table | Rows |
|---|---|
| `monitoring_pricing_policy_recommendations` | 398,474 |
| `monitoring_policy_backtest_summary` | 7 (6 fuel types + `ALL`) |
| Distinct stations covered | 1,905 |
| Date range written | 2025-12-27 -> 2026-06-30 (matches config exactly) |

### 6b. HOLD / FOLLOW / LEAD counts, policy vs. always-HOLD baseline

| Fuel | Mode | Rows | Policy HOLD | FOLLOW | LEAD | Baseline HOLD |
|---|---|---|---|---|---|---|
| DL | automated | 31,554 | 5,889 | 24,996 | 669 | 31,554 (100%) |
| E10 | automated | 77,414 | 35,212 | 38,500 | 3,702 | 77,414 (100%) |
| P95 | **watch_only** | 59,998 | 30,253 | 29,745 | **0** | 59,998 (100%) |
| P98 | automated | 87,939 | 35,260 | 47,674 | 5,005 | 87,939 (100%) |
| PDL | automated | 56,678 | 13,140 | 42,645 | 893 | 56,678 (100%) |
| U91 | **watch_only** | 84,891 | 42,562 | 42,329 | **0** | 84,891 (100%) |
| **ALL** | mixed | 398,474 | 162,316 | 225,889 | 10,269 | 398,474 (100%) |

Confirms §4: LEAD is exactly 0 for both watch-only fuel types, never triggered by
construction, across all 398,474 rows.

### 6c. Guardrail interventions - concentrated exactly where TGP exists

| Fuel | Guardrail interventions | As % of that fuel's FOLLOW count |
|---|---|---|
| DL | 14,975 | 59.9% |
| U91 | 19,322 | 45.6% |
| E10 / P95 / P98 / PDL | 0 | n/a (no TGP data to guard against - §5) |

For DL and U91, the margin guardrail is not a rare edge case - it caps roughly half
of all FOLLOW recommendations, meaning the fully-competitive target price (matching
the local competitor median) would frequently have breached the margin floor.

### 6d. Indicative margin difference (DL and U91 only - §5)

Policy's hypothetical margin (what the recommended price would indicatively yield)
minus the actual observed indicative margin that day, summed/averaged across every
row - **not a revenue or profit figure** (no volume data):

| Fuel | Rows with margin data | Avg difference (cpl) | Total difference (cpl) |
|---|---|---|---|
| DL | 31,554 | -11.04 | -348,315.1 |
| U91 | 84,891 | -5.66 | -480,631.6 |
| E10 / P95 / P98 / PDL | 0 | n/a - no TGP data | n/a |
| **ALL** (DL+U91 pooled) | 116,445 | -7.12 | -828,946.7 |

The negative average is expected and not alarming on its own: FOLLOW recommendations
are, by definition, giving up margin to stay competitive, and the guardrail (§6c)
already caps the worst of it. The number is reported here as a magnitude check, not a
target to minimize - a policy that never gave up margin would also never FOLLOW.

### 6e. Stale-price days: policy vs. baseline

A day counts as stale once the *actual* observed price has gone unchanged for at
least `stale_price_days_threshold` (7) consecutive days. Baseline (always HOLD) is
stale on every day meeting that raw condition; the policy is only "still stale" on
days it also recommended HOLD (i.e., failed to catch a needed correction):

| Fuel | Stale days (baseline) | Stale days (policy) | Reduction |
|---|---|---|---|
| DL | 407 | 206 | 49.4% |
| E10 | 746 | 511 | 31.5% |
| P95 | 233 | 227 | 2.6% |
| P98 | 457 | 297 | 35.0% |
| PDL | 622 | 419 | 32.6% |
| U91 | 605 | 526 | 13.1% |
| **ALL** | 3,070 | 2,186 | 28.8% |

P95 and U91 (watch-only) show the smallest reductions - consistent with §4: without
jump-driven LEAD, their only lever against staleness is the FOLLOW/forecast path.

### 6f. Days priced materially above competitors: actual vs. unaddressed

"Unaddressed" = the station was priced >= `follow_min_overpriced_cpl` above the local
competitor median that day, **and** the policy did not recommend FOLLOW:

| Fuel | Days priced above competitors (actual) | Left unaddressed by policy |
|---|---|---|
| DL | 5,607 | 48 (0.9%) |
| E10 | 14,227 | 0 (0.0%) |
| P95 | 11,674 | 0 (0.0%) |
| P98 | 19,567 | 0 (0.0%) |
| PDL | 13,541 | 0 (0.0%) |
| U91 | 15,579 | 374 (2.4%) |
| **ALL** | 80,195 | 422 (0.5%) |

**Read this carefully**: the 0.0% for E10/P95/P98/PDL is mechanical, not a policy
achievement - with no TGP data, the guardrail can never block their FOLLOW, so every
overpriced day is addressed by construction. DL and U91's small nonzero rates are the
*meaningful* number here: the margin guardrail occasionally (and correctly, by
design) leaves a station overpriced rather than cutting into an unsafe margin.

### 6g. Forecast and jump-signal contribution

Row count where each signal actually participated in the decision
(`jump_signal_used` / `forecast_signal_used` on `decide_policy`'s output):

| Fuel | Jump-signal-driven rows | Forecast-signal-driven rows |
|---|---|---|
| DL | 1,777 | 25,629 |
| E10 | 11,730 | 42,751 |
| P95 | 0 (watch-only) | 27,750 |
| P98 | 17,374 | 42,485 |
| PDL | 2,501 | 39,553 |
| U91 | 0 (watch-only) | 46,459 |
| **ALL** | 33,382 | 224,627 |

The forecast signal contributes to roughly 4-7x as many decisions as the jump signal
across every automated fuel type - FOLLOW's two triggers (competitor positioning,
proactive forecast decline) fire far more often than the stricter three-condition
LEAD trigger, by design (§3).

### 6h. LEAD hit rate: did an actual jump follow, vs. the base rate

For every LEAD recommendation, whether `gold_price_jump_labels.jump_within_48h` was
actually `True` for that row (ground truth, never used as a LEAD input - a genuine
outcome check) - compared against each fuel type's overall jump base rate in the same
window:

| Fuel | LEAD count | LEAD hit rate | Base jump rate | Lift |
|---|---|---|---|---|
| DL | 669 | 30.0% | 14.7% | 2.04x |
| E10 | 3,702 | 15.6% | 9.1% | 1.70x |
| P98 | 5,005 | 21.7% | 7.5% | 2.90x |
| PDL | 893 | 49.2% | 19.8% | 2.48x |
| **ALL automated** | 10,269 | 22.4% | - | - |

Every automated fuel type's LEAD recommendations were followed by an actual jump at
1.7-2.9x the base rate - the jump signal is doing real work, not just riding the
overall jump frequency.

## 7. Limitations

- **No revenue, profit, or P&L claim anywhere** - no sales-volume data exists; every
  margin number is indicative (`retail_price - TGP`), consistent with
  `gold_indicative_margin`'s existing scope note.
- **TGP margin guardrail covers only DL and U91** (§5) - for E10/P95/P98/PDL, FOLLOW
  recommendations have no margin floor at all. This is the single most important
  caveat in this phase; any production use of the FOLLOW recommendation for those
  four fuel types needs a different guardrail (or an accepted TGP data gap) before
  going live.
- **Per-day independent recommendations, not a compounding trajectory simulation**
  (§2) - the backtest never assumes yesterday's recommendation was adopted when
  generating today's. A live deployment that actually changes prices day-over-day
  would compound differently than this backtest measures.
- **U91 and P95 never receive LEAD** (§4) - by design, not a gap to fix casually;
  their jump signal was independently judged too weak in Phase 3.
- **Market-level forecast applied per station** - the 3-day/7-day forecast is
  fuel_type x date (market-level, matching Phase 3's own grain), broadcast to every
  station of that fuel type on that date. Station-specific forecast error is not
  modelled.
- **One-time forecast model fit** (§1) - the persisted 3-day/7-day regressors were
  fit once for this backtest, not walk-forward validated fold-by-fold the way Phase
  3's accuracy numbers were; their accuracy characteristics should be assumed to
  match Phase 3's reported numbers, not independently re-verified here.
- **No live deployment** - this is a backtest against historical Gold data, not a
  system generating recommendations against live prices today.

## 8. MLflow and Databricks references

- Experiment: `/Shared/fuelsignal-pricing-policy`, run `9a660be77e5f4e24a0749e270bc5b3d8`.
- Params logged: fuel-type sets, backtest window, jump model ID/source run, all five
  policy thresholds, code version.
- Artifacts: `pricing_policy_backtest_summary.json` (corrected, §5), two logged
  LightGBM models (`forecast_model_h3`, `forecast_model_h7`).
- Tables: `fuelsignal.fuelsignal_monitoring.monitoring_pricing_policy_recommendations`
  (row-level), `fuelsignal.fuelsignal_monitoring.monitoring_policy_backtest_summary`
  (aggregate).
