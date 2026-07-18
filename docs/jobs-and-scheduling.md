# Databricks Jobs and Scheduling (Week 2 Phase 5-6 - Operationalisation)

Deployed live 2026-07-18 by `scripts/deploy_databricks_jobs.py`, validated live via
`run-now`, and **activated** (schedules UNPAUSED) the same day. This workspace is
serverless-only - confirmed live via `GET /api/2.0/clusters/list` (returns no
clusters) and `GET /api/2.0/sql/warehouses` (one Serverless SQL Warehouse only) -
so every job task runs as a `spark_python_task` against a serverless job
environment, with `git_source` pointing at this repository's `main` branch so a job
always runs whatever is currently on `main`, never a stale uploaded copy.

## 1. Two jobs, four tasks, mapped onto the five requested pipeline stages

The task named "jump scoring" and "3-day/7-day forecasting" are one combined task,
not two - `scripts/score_daily.py` already does both in a single pass, reusing the
same models the policy needs.

| Job | Task (in order) | Script | Depends on |
|---|---|---|---|
| `fuelsignal-daily-pipeline` | `ingest_sources` | `scripts/run_ingestion_pipeline.py` | - |
| `fuelsignal-daily-pipeline` | `refresh_gold` | `scripts/run_gold_pipeline.py` | `ingest_sources` |
| `fuelsignal-daily-pipeline` | `score_jump_and_forecast` | `scripts/score_daily.py` | `refresh_gold` |
| `fuelsignal-monitoring-checks` | `validate_pipeline` | `scripts/validate_live_pipeline.py` | - (standalone) |

`fuelsignal-monitoring-checks` is a separate job, not a fourth task on the same
chain, so it can report pipeline status even when the main pipeline fails partway
through.

## 2. Live job IDs, schedules, and validation status

| Job | Job ID | Cron | Timezone | Status |
|---|---|---|---|---|
| `fuelsignal-daily-pipeline` | `507451964880120` | `0 0 5 * * ?` (05:00 daily) | Australia/Sydney | **UNPAUSED, live-validated** |
| `fuelsignal-monitoring-checks` | `528130432834470` | `0 30 5 * * ?` (05:30 daily) | Australia/Sydney | **UNPAUSED, live-validated** |

Both jobs were run manually via `run-now` end to end - every task succeeded - before
their schedules were switched on. Verified in that validation run:

- **Auth worked**: `databricks_auth()` successfully obtained credentials inside the
  running task (via the `dbutils.secrets` fallback - see SS3).
- **All tasks finished successfully**: `ingest_sources` -> `refresh_gold` ->
  `score_jump_and_forecast` all reported `TERMINATED` / `SUCCESS`; so did
  `validate_pipeline`.
- **Expected tables updated**: `monitoring_pricing_policy_recommendations` gained
  1,739 fresh rows for the newly-scored date (2026-06-30 at validation time);
  `monitoring_pipeline_runs` gained new audit rows for the ingestion run.
- **No duplicate recommendations**: the table's total row count was unchanged
  before/after (398,474) - `score_daily.py`'s delete-then-insert upsert by
  `market_date` replaced exactly that day's rows, nothing else.
- **Run metadata logged**: both the Databricks job run history and this run's
  `monitoring_pipeline_runs` audit rows, plus an MLflow run
  (`score-daily-<timestamp>`) in `/Shared/fuelsignal-pricing-policy`.

## 3. Credential provisioning (completed 2026-07-18, nothing committed to git)

A dedicated long-lived PAT ("fuelsignal-scheduled-jobs", 90-day lifetime, expires
2026-10-16) was created and stored as the Databricks secret `fuelsignal/token`:

```bash
databricks secrets create-scope fuelsignal
databricks secrets put-secret fuelsignal token   # pasted the PAT, never in git
```

The token value itself was written to a local temp file only long enough to call
the Secrets API, then deleted (`shred -u`); it is not in this repository, any
commit, or any log this document quotes. **Creating this credential was
deliberately scoped as a distinct, explicit step** - not something done silently as
a side effect of "deploy the jobs" - since minting a new standing credential on a
live account is a materially different kind of action from writing code or running
a backtest. An earlier attempt in an unattended context was in fact blocked by this
session's own safety tooling; it was completed here only once explicitly
instructed to.

## 4. Bugs found and fixed via live `run-now` validation

None of these were visible from local execution, or from job/environment
*creation* succeeding - each needed an actual `run-now` to surface. Documented here
so the failure signature is recognisable if it recurs (e.g. after a Databricks
platform update):

1. **`environments[].spec.client: "1"` fails to launch** -
   `"Invalid platform channel Client-1"`. Fixed: use `"2"`.
2. **`spark_python_task` without `"source": "GIT"`** looks for `python_file` in the
   workspace filesystem instead of the git checkout - `"Cannot read the python
   file"`. Fixed: add `"source": "GIT"` to every task.
3. **`__file__` is undefined** inside a git-sourced task (it executes through an
   exec-style, non-notebook context, confirmed by the traceback path
   `~/.ipykernel/.../command--...`) - every script's `PROJECT_ROOT = Path(__file__)`
   raised `NameError`. Fixed: fall back to walking up from `Path.cwd()` looking for
   `pyproject.toml` - **not** a naive `PROJECT_ROOT = Path.cwd()`, which is wrong
   too (see #4).
4. **`Path.cwd()` is the script's own containing directory** (e.g. `.../scripts`),
   not the repo root - a naive `Path.cwd()` fallback produced a doubled path
   (`.../scripts/scripts/run_gold_pipeline.py`) and would have broken every
   `PROJECT_ROOT / "config" / "*.yml"` read. Fixed: walk up looking for
   `pyproject.toml` (see #3) instead of assuming any specific cwd.
5. **`spark_env_vars` does not reach the process environment** for a serverless
   `spark_python_task` (`DATABRICKS_HOST`/`DATABRICKS_TOKEN` were empty inside the
   task despite being set on the task definition). Fixed: pass credentials via
   `spark_python_task.parameters` instead - but see #6, that alone wasn't enough.
6. **`{{secrets/scope/key}}` templating does not resolve inside
   `spark_python_task.parameters`** on this workspace - the literal unsubstituted
   string was received as the token, producing an HTTP 401 "Credential was not
   sent." Fixed: `databricks_auth()` detects an unsubstituted `{{` prefix and falls
   back to `databricks.sdk.runtime.dbutils.secrets.get()`, the documented,
   reliable way to read a secret from inside a running Databricks job.
7. **`raise SystemExit(0)` on success is treated as task FAILURE** under this
   execution context - a script that printed a full success summary and returned 0
   was still marked `FAILED`. Fixed: every script's
   `if __name__ == "__main__":` block now only raises `SystemExit` when the exit
   code is actually non-zero; falling through on success is behaviourally
   identical for local/CLI execution.
8. **`find_latest_forecast_models` picked up the wrong MLflow run** - a naive
   "most recent finished run in the experiment" query found
   `scripts/score_daily.py`'s own prior daily-scoring run (which never logs a
   forecast model) instead of an actual backtest run, and failed with "missing a
   forecast_model_h3/h7 artifact." Fixed: filter to
   `tags.phase = 'week2-phase5-pricing-policy-operationalisation'` (the tag
   `run_pricing_policy_backtest.py` sets on its own run), checking up to 5
   candidates rather than just the newest.

## 5. Data pull cost (Phase 6 - resolved)

Phase 5's `scripts/score_daily.py` originally pulled the full multi-year Gold
archive purely to score the single latest day. Phase 6 added an optional
`since_date` parameter to `fetch_training_data`/`fetch_market_price_series`
(`scripts/train_jump_model.py`, `scripts/forecast_prices.py` - default `None`,
every walk-forward-validated caller unaffected) and a cheap
`fetch_latest_eligible_market_date` scalar query, so `score_daily.py` now pulls
only a 60-day trailing window (headroom over the 14-day rolling-feature window and
the 5-8.5 day empirical inter-jump cycle length) - live-verified: 127,171 rows
pulled instead of 839,906, to correctly score the same 1,739 station-fuel rows. A
station whose actual last price change was more than 60 days before the score date
will have `days_since_price_change` under-counted (measured from the window start,
not the true last change) - accepted as a rare-case approximation, since 60 days
already dwarfs the 7-day `stale_price_days_threshold`.

## 6. Known remaining gap: FuelCheck station-reference credentials

`ingest_sources`' station-reference refresh sub-step
(`FUELCHECK_API_KEY`/`FUELCHECK_API_SECRET`) is **not** provisioned as a Databricks
secret - only the Databricks Jobs credential (SS3) was. This sub-step fails
gracefully by design (`ingest_station_reference` catches its own errors and
returns a dict with an `"error"` key; `main()` still returns 0), so it does not
fail the `ingest_sources` task or block `refresh_gold`/`score_jump_and_forecast` -
but station coordinates will not be refreshed by the scheduled job until these
credentials are also provisioned the same way (a new secret key in the
`fuelsignal` scope, plus a small `databricks_auth()`-style fallback added to
however that ingester reads them). Bronze/Silver/Gold refresh and price/jump
scoring are unaffected - they rely on already-resolved station coordinates in
`silver_station_master`, not on this sub-step running successfully every day.

## 7. Re-running the job deploy script

`scripts/deploy_databricks_jobs.py` is idempotent - re-running it looks up each job
by name and calls `jobs/reset` to update it in place. `SCHEDULE_PAUSE_STATUS` at
the top of the script is `"UNPAUSED"` (reflecting the current validated,
activated state) - if a future change is serious enough to warrant re-validation
before trusting the schedule again, temporarily set it to `"PAUSED"`, re-run the
deploy script, validate with `run-now`, then switch it back.
