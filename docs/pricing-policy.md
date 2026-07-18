# Pricing Policy and Six-Month Backtest (Week 2 Phase 4-5)

Phase 4 executed live 2026-07-18; Phase 5 (operationalisation - the three-way
`recommendation_status` safety gate, the re-tuned margin guardrail, and the
dashboard-ready output) re-ran the same backtest live the same day by
`scripts/run_pricing_policy_backtest.py`. Tracked in MLflow experiment
`/Shared/fuelsignal-pricing-policy`, run ID `519233c8d9fa4d3695d633c76d7ec8d5`
(current/latest - supersedes the Phase 4 run `9a660be77e5f4e24a0749e270bc5b3d8`).
Row-level recommendations are in
`fuelsignal.fuelsignal_monitoring.monitoring_pricing_policy_recommendations`
(398,474 rows, full-refreshed on every re-run - never an accumulating multi-version
log); the aggregate comparison against an always-HOLD baseline is in
`monitoring_policy_backtest_summary` (7 rows); the current per-fuel-type automation
configuration is in `monitoring_fuel_policy_status` (6 rows, new in Phase 5). Full
numbers are also in `config/pricing_policy_backtest_results.json`.

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
  and logs both to MLflow. This is a one-time artifact of already-validated
  methodology, not a retrain or redesign.

## 2. Backtest window: the entire leakage-safe span available

`config/pricing_policy.yml`: `backtest_start_date: 2025-12-27`,
`backtest_end_date: 2026-06-30` - 186 days (~6.1 months), the *entire* out-of-sample
span after the jump classifier's train cutoff through the current end of the Gold
archive. The policy is applied independently to every eligible station-fuel-day in
this window - a leakage-safe, per-day recommendation generation, not a compounding
price-trajectory simulation.

## 3. HOLD / FOLLOW / LEAD rules

Implemented in `src/fuelsignal/policy/pricing_policy.py` (13 unit tests), numeric
thresholds versioned in `config/pricing_policy.yml`. Precedence:

1. **LEAD** - only for automated fuel types (§4), only when the calibrated jump
   probability clears its Phase 3 threshold **and** the 3-day forecast confirms a
   rise of at least `lead_min_forecast_change_cpl` (5.0 cpl) **and** the station is
   not already priced above its local competitor median. The recommended move is a
   modest `lead_step_cpl` (2.0 cpl) anticipatory raise, not the full forecast move.
2. **FOLLOW** - triggered reactively once a station is priced at least
   `follow_min_overpriced_cpl` (4.0 cpl) above its local competitor median, or
   proactively if the 3-day forecast predicts a decline of at least
   `follow_forecast_decline_cpl` (5.0 cpl). The **TGP margin guardrail** (§5) can cap
   how far a FOLLOW is allowed to cut price.
3. **HOLD** - the default when nothing above triggers, and whenever
   `current_price_cpl` is missing.

## 4. Three-way safety gate: `recommendation_status` (Phase 5)

Phase 4 shipped with a two-way `mode` (`automated`/`watch_only`) that only captured
*jump-model* eligibility. Phase 5's explicit instruction - "allow full policy
automation only where both model eligibility and a valid margin guardrail are
present" - needed a second, independent dimension: does a validated TGP margin
guardrail even exist for this action. `PolicyDecision.recommendation_status`
(`src/fuelsignal/policy/pricing_policy.py`) is the result, computed independently of
`action`:

- **`automated`** - safe to surface as an automated recommendation. HOLD when the
  fuel type has jump-model automation enabled; LEAD always (raising price cannot
  breach a margin floor); FOLLOW only when TGP data exists to guard the cut.
- **`watch_only`** - informational, human-review-only. HOLD/FOLLOW for a fuel type
  without jump-model automation, whenever TGP data *is* available to guard the
  FOLLOW (U91's case).
- **`disabled_unsafe`** - never actionable. Any FOLLOW where TGP data is
  unavailable - regardless of how reliable that fuel type's jump signal is. `action`
  still reports what the raw rule would recommend (never silently rewritten to
  HOLD, so the underlying signal stays visible and auditable); `recommendation_status`
  is what a dashboard or downstream automation must gate on.

### 4a. The resulting per-fuel-type matrix (`monitoring_fuel_policy_status`, live)

| Fuel | Jump-model eligible | TGP guardrail valid | LEAD enabled | FOLLOW automation status |
|---|---|---|---|---|
| DL | Yes | Yes | Yes | **automated** |
| U91 | No | Yes | No | **watch_only** |
| E10 | Yes | No | Yes | **disabled_unsafe** |
| P98 | Yes | No | Yes | **disabled_unsafe** |
| PDL | Yes | No | Yes | **disabled_unsafe** |
| P95 | No | No | No | **disabled_unsafe** |

**DL is the only fuel type with full automation** (both LEAD and FOLLOW automated).
E10/P98/PDL get automated LEAD but `disabled_unsafe` FOLLOW - their jump signal
cleared Phase 3's business rule, but there is no TGP data to protect a price cut.
P95 is the strictest case: neither dimension clears, so its FOLLOW recommendation is
`disabled_unsafe` for a second, independent reason on top of never receiving LEAD.
U91 is unchanged from Phase 4's "watch-only, as already defined" - TGP exists so its
FOLLOW is informational/`watch_only`, never `disabled_unsafe`, but LEAD is never
issued because its jump signal is unreliable.

## 5. TGP margin guardrail: coverage limit and the re-tuned floor

**This guardrail can only function where TGP data exists.** Verified live: `tgp_cpl`
is 100% non-null for DL and U91, 100% null for E10, P95, P98, and PDL (Phase 1 - TGP
source data only maps to those two fuel types). This is the reason §4's matrix looks
the way it does, and remains the single most important limitation of this phase - see
`docs/margin-proxy-investigation.md` for why a synthetic proxy was investigated and
explicitly not activated.

### 5a. Re-tuned floor: 1.0 -> 2.0 cpl

`min_margin_guardrail_cpl` was re-tuned from Phase 4's 1.0 cpl to **2.0 cpl**, per
the explicit instruction to re-examine the stale-price-reduction vs. margin-loss
trade-off for DL and U91. Method: a grid sweep (0.0-5.0 cpl, step 0.5) re-scored the
*already-collected* Phase 4 backtest inputs through the unchanged `decide_policy()`
rule (no live re-pull needed for the sweep itself) - full table below.

| Floor (cpl) | DL avg margin diff | DL stale-days-policy | U91 avg margin diff | U91 stale-days-policy |
|---|---|---|---|---|
| 0.0 | -11.48 | 203 | -5.87 | 525 |
| 1.0 (Phase 4) | -11.04 | 206 | -5.66 | 526 |
| **2.0 (chosen)** | **-10.59** | **216** | **-5.44** | **526** |
| 3.0 | -10.13 | 221 | -5.19 | 526 |
| 5.0 | -9.20 | 249 | -4.68 | 527 |

Raising the floor from 1.0 to 2.0 cpl improves DL's average margin difference by 0.45
cpl and U91's by 0.22 cpl, for a cost of +10 stale-price days for DL (out of 31,554
rows, +0.03pp) and effectively zero measurable change for U91. Floors above ~3.0 cpl
start costing meaningfully more staleness (DL reaches 249 stale days by floor=5.0)
for diminishing margin gains - 2.0 cpl sits in the efficient part of the curve, not at
either extreme. The live re-run's actual results (§6) match this grid analysis
exactly (DL guardrail interventions 15,528, avg margin -10.59; U91 guardrail
interventions 21,401, avg margin -5.44 - both identical to the sweep's predictions).

## 6. Six-month backtest results (2025-12-27 -> 2026-06-30, current/Phase 5 run)

### 6a. Databricks row counts

| Table | Rows |
|---|---|
| `monitoring_pricing_policy_recommendations` | 398,474 |
| `monitoring_policy_backtest_summary` | 7 (6 fuel types + `ALL`) |
| `monitoring_fuel_policy_status` | 6 (one per fuel type) |
| Distinct stations covered | 1,905 |
| Date range written | 2025-12-27 -> 2026-06-30 (matches config exactly) |

### 6b. HOLD / FOLLOW / LEAD counts, and the recommendation_status breakdown

| Fuel | Mode | Rows | HOLD | FOLLOW | LEAD | automated | watch_only | disabled_unsafe |
|---|---|---|---|---|---|---|---|---|
| DL | automated | 31,554 | 6,093 | 24,792 | 669 | **31,554** | 0 | 0 |
| E10 | automated | 77,414 | 35,212 | 38,500 | 3,702 | 38,914 | 0 | 38,500 |
| P95 | watch_only | 59,998 | 30,253 | 29,745 | **0** | 0 | 30,253 | 29,745 |
| P98 | automated | 87,939 | 35,260 | 47,674 | 5,005 | 40,265 | 0 | 47,674 |
| PDL | automated | 56,678 | 13,140 | 42,645 | 893 | 14,033 | 0 | 42,645 |
| U91 | watch_only | 84,891 | 42,902 | 41,989 | **0** | 0 | **84,891** | 0 |
| **ALL** | mixed | 398,474 | 162,860 | 225,345 | 10,269 | 124,766 | 115,144 | 158,564 |

DL is the only fuel type with `disabled_unsafe = 0` across every row - the only one
where every recommendation, HOLD/FOLLOW/LEAD alike, is safe to automate today. LEAD
stays exactly 0 for both P95 and U91.

### 6c. Guardrail interventions (2.0 cpl floor) - concentrated exactly where TGP exists

| Fuel | Guardrail interventions | As % of that fuel's FOLLOW count |
|---|---|---|
| DL | 15,528 | 62.6% |
| U91 | 21,401 | 51.0% |
| E10 / P95 / P98 / PDL | 0 | n/a (no TGP data to guard against - §5) |

### 6d. Indicative margin difference (DL and U91 only - §5)

Not a revenue or profit figure (no volume data) - policy's hypothetical margin minus
the actual observed indicative margin, at the re-tuned 2.0 cpl floor:

| Fuel | Rows with margin data | Avg difference (cpl) | Total difference (cpl) |
|---|---|---|---|
| DL | 31,554 | -10.59 | -334,088.9 |
| U91 | 84,891 | -5.44 | -461,391.5 |
| E10 / P95 / P98 / PDL | 0 | n/a - no TGP data | n/a |
| **ALL** (DL+U91 pooled) | 116,445 | -6.83 | -795,480.4 |

### 6e. Stale-price days: policy vs. baseline

| Fuel | Stale days (baseline) | Stale days (policy) | Reduction |
|---|---|---|---|
| DL | 407 | 216 | 46.9% |
| E10 | 746 | 511 | 31.5% |
| P95 | 233 | 227 | 2.6% |
| P98 | 457 | 297 | 35.0% |
| PDL | 622 | 419 | 32.6% |
| U91 | 605 | 526 | 13.1% |
| **ALL** | 3,070 | 2,196 | 28.5% |

E10/P95/P98/PDL are unaffected by the margin-guardrail re-tune (they have no TGP
data for the guardrail to act on in the first place) - their numbers are identical
to Phase 4.

E10/P95/P98/PDL's stale-price behavior is unaffected by the margin-guardrail re-tune
(they have no TGP data for the guardrail to act on in the first place); their Phase 4
numbers (511/227/297/419 stale-days-policy respectively, vs. baseline 746/233/457/622)
are unchanged by this re-run.

### 6f. LEAD hit rate: unaffected by the guardrail re-tune

LEAD logic was not touched by the Phase 5 re-tune (the guardrail only affects
FOLLOW), so hit rates are identical to Phase 4: DL 30.0% vs. 14.7% base rate (2.04x),
E10 15.6% vs. 9.1% (1.70x), P98 21.7% vs. 7.5% (2.90x), PDL 49.2% vs. 19.8% (2.48x).
Every automated fuel type's LEAD recommendations were followed by an actual jump at
1.7-2.9x the base rate.

## 7. Dashboard-ready output (Phase 5)

Four views, not tables, deployed by `deploy_dashboard_schema()` in the backtest
script - views always reflect the latest `monitoring_pricing_policy_recommendations`
data with no separate population/refresh step to keep in sync:

- **`monitoring_pricing_dashboard`** - every row, joined to `silver_station_master`
  for `station_name`/`brand`/`suburb`/`postcode`/`latitude`/`longitude` (map and
  brand-slicer ready), plus a `warning_message` column: non-null and human-readable
  whenever `recommendation_status` is `watch_only` or `disabled_unsafe`, explaining
  exactly why in plain language (e.g. *"No validated TGP margin guardrail exists for
  E10 - shown for visibility only, do not act on it automatically."*).
- **`monitoring_pricing_dashboard_automated`** / **`..._watch_only`** /
  **`..._disabled_unsafe`** - the same view pre-filtered by
  `recommendation_status`, so a Power BI report (or any BI tool) can bind three
  separate, unambiguous data sources instead of relying on report authors to apply
  the filter correctly themselves. Live row counts: automated 124,766; watch_only
  115,144; disabled_unsafe 158,564 (sums to 398,474).

`monitoring_fuel_policy_status` (§4a) is the fifth dashboard-facing object - a small,
one-row-per-fuel-type configuration table for a report's "current policy" summary
page, rather than requiring a report author to re-derive the matrix from 398K detail
rows.

## 8. Limitations

- **No revenue, profit, or P&L claim anywhere** - no sales-volume data exists.
- **TGP margin guardrail covers only DL and U91** (§5) - the single most important
  caveat in this phase. `docs/margin-proxy-investigation.md` investigated a
  retail-spread-based proxy and explicitly did not activate it (no ground truth to
  validate against).
- **Per-day independent recommendations, not a compounding trajectory simulation.**
- **U91 and P95 never receive LEAD** - by design, not a gap to fix casually.
- **Market-level forecast applied per station** - broadcast to every station of a
  fuel type on a date; station-specific forecast error is not modelled.
- **One-time forecast model fit** - not walk-forward validated fold-by-fold the way
  Phase 3's accuracy numbers were.
- **No live deployment yet** - `scripts/score_daily.py` and the Databricks Jobs
  (`docs/jobs-and-scheduling.md`) exist and were validated structurally, but the
  jobs' schedules are PAUSED pending a human-provisioned credential - see that doc
  for exactly what remains.
- **`score_daily.py` pulls the full archive to score one day** - a known, documented
  performance limitation, not a correctness issue (`docs/jobs-and-scheduling.md` §4).

## 9. MLflow and Databricks references

- Experiment: `/Shared/fuelsignal-pricing-policy`, current run
  `519233c8d9fa4d3695d633c76d7ec8d5` (Phase 5, supersedes Phase 4's
  `9a660be77e5f4e24a0749e270bc5b3d8`).
- Params logged: fuel-type sets, backtest window, jump model ID/source run, all five
  policy thresholds (including the re-tuned `min_margin_guardrail_cpl=2.0`), code
  version.
- Artifacts: `pricing_policy_backtest_summary.json`, two logged LightGBM models
  (`forecast_model_h3`, `forecast_model_h7`).
- Tables/views: `monitoring_pricing_policy_recommendations` (row-level, full-refreshed
  every run), `monitoring_policy_backtest_summary` (aggregate),
  `monitoring_fuel_policy_status` (current config), plus the four dashboard views in
  §7. All in `fuelsignal.fuelsignal_monitoring`.
- Databricks Jobs: `docs/jobs-and-scheduling.md` - two jobs deployed live
  (`fuelsignal-daily-pipeline` job ID `507451964880120`,
  `fuelsignal-monitoring-checks` job ID `528130432834470`), both scheduled but
  PAUSED pending credential provisioning.
