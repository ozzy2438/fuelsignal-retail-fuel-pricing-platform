# Databricks Jobs and Scheduling (Week 2 Phase 5 - Operationalisation)

Deployed live 2026-07-18 by `scripts/deploy_databricks_jobs.py`. This workspace is
serverless-only - confirmed live via `GET /api/2.0/clusters/list` (returns no
clusters) and `GET /api/2.0/sql/warehouses` (one Serverless SQL Warehouse only) -
so every job task below runs as a `spark_python_task` against a serverless job
environment (`environment_key`, no `new_cluster`/`existing_cluster_id`), with
`git_source` pointing at this repository's `main` branch so a job always runs
whatever is currently on `main`, never a stale uploaded copy.

## 1. Two jobs, four tasks, mapped onto the five requested pipeline stages

The task named "jump scoring" and "3-day/7-day forecasting" are one combined task,
not two - `scripts/score_daily.py` already does both in a single pass (it scores the
jump probability and both forecast horizons together, reusing the same models the
policy needs), so splitting it into two separate jobs would only add coordination
overhead with no benefit.

| Job | Task (in order) | Script | Depends on |
|---|---|---|---|
| `fuelsignal-daily-pipeline` | `ingest_sources` | `scripts/run_ingestion_pipeline.py` | - |
| `fuelsignal-daily-pipeline` | `refresh_gold` | `scripts/run_gold_pipeline.py` | `ingest_sources` |
| `fuelsignal-daily-pipeline` | `score_jump_and_forecast` | `scripts/score_daily.py` | `refresh_gold` |
| `fuelsignal-monitoring-checks` | `validate_pipeline` | `scripts/validate_live_pipeline.py` | - (standalone) |

`fuelsignal-monitoring-checks` is a separate job, not a fourth task on the same
chain, specifically so it can report pipeline status even when the main pipeline
fails partway through - a task chained after a failed upstream task would never run
and the monitoring signal would go dark exactly when it's needed most.

Bronze and Silver population happens inside `ingest_sources`
(`run_ingestion_pipeline.py`'s own docstring: "the live, idempotent FuelSignal
Bronze and Silver pipeline") - `refresh_gold` only rebuilds the Gold layer from the
Silver tables `ingest_sources` just refreshed, per the existing dependency chain
documented in `docs/feature-engineering.md`.

## 2. Live job IDs and schedules

| Job | Job ID | Cron | Timezone | Status |
|---|---|---|---|---|
| `fuelsignal-daily-pipeline` | `507451964880120` | `0 0 5 * * ?` (05:00 daily) | Australia/Sydney | **PAUSED** |
| `fuelsignal-monitoring-checks` | `528130432834470` | `0 30 5 * * ?` (05:30 daily) | Australia/Sydney | **PAUSED** |

The monitoring job is scheduled 30 minutes after the pipeline job starts, giving the
three-task pipeline (ingestion -> Gold -> scoring) time to finish under normal
conditions before the freshness check runs.

## 3. Why both schedules are PAUSED - and the one thing that has to happen before they can run at all

Both jobs are fully defined and were created successfully via the live Databricks
Jobs API (`jobs/create`, confirmed via `jobs/get` immediately after) - the schedule
*is* configured, exactly as requested, but its `pause_status` is `PAUSED` rather than
active. This was a deliberate choice, not an oversight, for two independent reasons:

1. **No credential exists yet for the jobs to authenticate with.** Every task calls
   `databricks_auth()` (`scripts/run_ingestion_pipeline.py`), which needs
   `DATABRICKS_HOST`/`DATABRICKS_TOKEN` in its process environment - there is no
   cluster-local `dbutils` session available to a serverless `spark_python_task`,
   and the CLI-OAuth fallback `databricks_auth()` otherwise uses only works on a
   machine with the Databricks CLI logged in, which a remote job run is not. Each
   task is configured with `spark_env_vars: {"DATABRICKS_HOST": "<this workspace's
   URL, not sensitive>", "DATABRICKS_TOKEN": "{{secrets/fuelsignal/token}}"}` - the
   `{{secrets/...}}` syntax is a live placeholder, but **no Databricks secret scope
   or secret was created**, so today every task would fail immediately on
   `databricks_auth()` if triggered.
2. **Creating the actual long-lived token was deliberately not done by the agent.**
   Provisioning a new standing credential on a live account, unattended, is a
   materially different kind of action from everything else in this phase (writing
   code, running a backtest, creating job *definitions*) - it is the one step here
   that is genuinely hard to reverse cleanly and that a human should explicitly see
   happen. The attempt to create one was in fact blocked by this session's own
   safety tooling before it executed, which is functioning as intended, not a bug to
   work around.

### Exact activation steps (for a human to run)

```bash
# 1. Create a personal access token scoped to this workspace (Databricks UI:
#    Settings -> Developer -> Access tokens -> Generate new token). Recommended:
#    give it a clear comment ("fuelsignal-scheduled-jobs") and a bounded lifetime
#    (e.g. 90 days), then set a calendar reminder to rotate it before expiry.

# 2. Create the secret scope and store the token (Databricks CLI):
databricks secrets create-scope fuelsignal
databricks secrets put-secret fuelsignal token   # paste the PAT from step 1

# 3. Unpause both schedules (or use the Databricks Jobs UI toggle):
databricks jobs update --job-id 507451964880120 \
  --json '{"fields_to_update": {"schedule": {"pause_status": "UNPAUSED"}}}'
databricks jobs update --job-id 528130432834470 \
  --json '{"fields_to_update": {"schedule": {"pause_status": "UNPAUSED"}}}'

# 4. Validate with a manual run before trusting the schedule:
databricks jobs run-now --job-id 507451964880120
databricks jobs run-now --job-id 528130432834470
```

### One more thing to verify at that point, not assumed here

`spark_env_vars` on a task is a well-established mechanism for cluster-based jobs;
this deploy script sets it on a *serverless* task and the Jobs API accepted the field
without error (`jobs/create` returned `200`), but that only confirms the API
validates the shape of the request - it does not confirm the environment variable is
actually injected into a serverless Python process at runtime, since `spark_env_vars`
has historically been a cluster-spec concept. **This should be treated as unverified
until the first real `run-now` after step 4 above is checked**: if `DATABRICKS_TOKEN`
does not reach `os.environ` inside the task, `databricks_auth()` will raise `OSError`
with a clear message, and the fallback is to have each script accept the token as a
CLI parameter instead (`spark_python_task.parameters`, which the Jobs API does
reliably support) and set it into `os.environ` at the top of `main()`.

## 4. Data pull cost - a known limitation, not a bug

`scripts/score_daily.py` pulls the full multi-year Gold archive
(`fetch_training_data`/`fetch_market_price_series`, the same functions the
backtest uses) purely to score the single latest day - `docs/pricing-policy.md`'s
backtest run shows this pull alone takes 5-10 minutes. A genuinely lightweight daily
job would push a `WHERE market_date >= ...` filter down into the SQL query instead
of filtering in pandas after the full pull; that requires adding a date-range
parameter to `fetch_training_data`/`fetch_market_price_series`
(`scripts/train_jump_model.py`, `scripts/forecast_prices.py`), which was not done in
this phase to avoid touching code shared with the already-validated Phase 2/3/4
scripts under time pressure. Worth doing before daily scheduling is trusted at scale
- today's `timeout_seconds: 3600` on `fuelsignal-daily-pipeline` gives real headroom,
but a filtered pull would make each run meaningfully cheaper and faster.

## 5. Re-running the job deploy script

`scripts/deploy_databricks_jobs.py` is idempotent - re-running it looks up each job
by name and calls `jobs/reset` to update it in place rather than creating a
duplicate. Safe to re-run after any change to the task list, dependencies, cron
schedule, or environment dependency versions.
