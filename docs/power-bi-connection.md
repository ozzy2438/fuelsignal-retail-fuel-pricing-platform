# Power BI Connection Instructions (Week 2 Phase 6)

Connects Power BI Desktop to the four dashboard-ready objects deployed in
`fuelsignal.fuelsignal_monitoring` (`docs/pricing-policy.md` SS7). All four are SQL
views (`monitoring_pricing_dashboard_automated`/`_watch_only`/`_disabled_unsafe`)
or a small reference table (`monitoring_fuel_policy_status`) queried live through
the same Databricks SQL warehouse everything else in this project uses - there is
no separate export or refresh step to keep in sync.

## 1. Connection details

| Field | Value |
|---|---|
| Server hostname | `dbc-aaefb4e4-e074.cloud.databricks.com` |
| HTTP path | `/sql/1.0/warehouses/fe0533e13373726a` |
| Port | `443` |
| Catalog | `fuelsignal` |
| Schema | `fuelsignal_monitoring` |
| Warehouse | `Serverless Starter Warehouse` (auto-resumes on query, `auto_stop_mins: 10`) |

## 2. Power BI Desktop steps

1. **Get Data -> More -> Database -> Azure Databricks** (the native Databricks
   connector, not a generic ODBC/JDBC entry - it handles the HTTP path and OAuth
   token exchange correctly).
2. **Server Hostname**: `dbc-aaefb4e4-e074.cloud.databricks.com`
3. **HTTP Path**: `/sql/1.0/warehouses/fe0533e13373726a`
4. **Data Connectivity mode**: `DirectQuery` recommended over `Import` - the
   underlying recommendations table is refreshed daily by
   `fuelsignal-daily-pipeline` (05:00 Australia/Sydney) and DirectQuery avoids a
   separate Power BI refresh schedule needing to be kept in sync with the job's.
   Use `Import` only if report interactivity/latency matters more than freshness
   for a given use case.
5. **Authentication**: Personal Access Token. Generate a PAT scoped to this
   workspace specifically for Power BI (Databricks UI: *Settings -> Developer ->
   Access tokens -> Generate new token*) - **do not reuse the
   `fuelsignal-scheduled-jobs` token** (`docs/jobs-and-scheduling.md`); that one is
   for the scheduled jobs' own authentication and should not be shared with a
   separate consumer, so each can be rotated or revoked independently without
   affecting the other.
6. In the Navigator, expand `fuelsignal -> fuelsignal_monitoring` and select the
   four objects below. Load or transform as needed; no additional joins are
   required (each view already includes station name/brand/location and a
   human-readable `warning_message`).

## 3. The four objects and how to use each

### 3a. `monitoring_pricing_dashboard_automated`

Recommendations safe to present as automated: full jump-model automation *and* a
validated TGP margin guardrail both present (`docs/pricing-policy.md` SS4a - today
this is DL only for FOLLOW, plus LEAD for DL/E10/P98/PDL). Bind this as the primary
table for any report page that implies "the system recommends acting on this."

### 3b. `monitoring_pricing_dashboard_watch_only`

Advisory-only recommendations - jump-model automation is off for that fuel type
(U91, P95) but a margin guardrail exists where relevant. Use a visually distinct
style (e.g. an amber banner, "Advisory - human review recommended") on any page
using this table, driven by `recommendation_status = 'watch_only'` and/or the
non-null `warning_message` column.

### 3c. `monitoring_pricing_dashboard_disabled_unsafe`

FOLLOW recommendations for fuel types with no validated TGP margin guardrail
(E10, P95, P98, PDL - `docs/pricing-policy.md` SS5). **Never bind this table to any
visual that could be read as an actionable recommendation.** The `action` column
still shows what the raw rule computed (for transparency/audit), but
`recommendation_status = 'disabled_unsafe'` and `warning_message` explain why it
must not be acted on. If this table is shown in a report at all, label the page
clearly (e.g. "Disabled - not for use") and consider restricting its visibility to
an admin/audit workspace role rather than a general audience.

### 3d. `monitoring_fuel_policy_status`

One row per fuel type - the current automation configuration
(`jump_model_eligible`, `tgp_margin_guardrail_valid`, `lead_enabled`,
`follow_automation_status`, `policy_notes`). Use this as the source for a small
"current policy configuration" card or table on a report's summary page, rather
than asking a report author to re-derive the automated/watch-only/disabled-unsafe
split from the 398K-row detail table.

## 4. Required dashboard warnings (do not omit)

Per the explicit instruction to add visible warnings explaining missing margin
coverage:

- Any page built on `monitoring_pricing_dashboard_disabled_unsafe` must surface
  the `warning_message` column text directly (not just imply it via color) - it
  states in plain language which fuel type lacks a validated margin guardrail and
  that the recommendation must not be acted on automatically.
- Any page built on `monitoring_pricing_dashboard_watch_only` must surface that
  fuel type's `warning_message` too - either the jump signal isn't reliable enough
  for automation, or (for P95, which appears in both the watch-only view for HOLD
  rows and the disabled-unsafe view for FOLLOW rows) both conditions apply.
- A recommended pattern: a single visual/table showing `recommendation_status`,
  `action`, and `warning_message` together, so a viewer never sees a bare
  "FOLLOW" without also seeing why it may or may not be safe to act on.

## 5. Data freshness

- `monitoring_pricing_policy_recommendations` (the table all four objects read
  from) is refreshed daily by the `score_jump_and_forecast` task of
  `fuelsignal-daily-pipeline`, scheduled 05:00 Australia/Sydney, upserting only
  the latest day (`docs/jobs-and-scheduling.md`).
- `monitoring_fuel_policy_status` changes only when the policy configuration
  itself changes (a new backtest run, a re-tuned threshold, etc.) - not on every
  daily job run.
- If a report needs to show "last updated," query
  `MAX(ingested_at)` from `monitoring_pricing_dashboard` directly, or reference
  `monitoring_pipeline_runs`/`monitoring_source_freshness` for the underlying data
  pipeline's own freshness signal.
