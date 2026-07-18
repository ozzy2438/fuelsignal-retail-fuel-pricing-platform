"""Deploy the daily FuelSignal pipeline and monitoring as Databricks Jobs (Week 2
Phase 5 - operationalisation).

This workspace is serverless-only (confirmed live 2026-07-18: `clusters/list`
returns no clusters, only a Serverless SQL Warehouse) - every task below runs as a
`spark_python_task` against a serverless job environment, with `git_source` pointing
at this repository's `main` branch so the job always runs the currently-deployed
code, never a stale uploaded copy.

Two jobs, four tasks, mapped onto the five requested pipeline stages (jump scoring
and 3/7-day forecasting are one combined task - scripts/score_daily.py already does
both in one pass, reusing the same reused-not-retrained models as the backtest):

- **fuelsignal-daily-pipeline** (sequential): ingest_sources -> refresh_gold ->
  score_jump_and_forecast. Each task depends on the previous one succeeding.
- **fuelsignal-monitoring-checks** (standalone): validate_pipeline. Runs
  independently so it can report status even if the main pipeline fails.

## Credential requirement - NOT completed by this script

Every task needs `DATABRICKS_HOST`/`DATABRICKS_TOKEN` in its execution environment
for `databricks_auth()` (scripts/run_ingestion_pipeline.py) to authenticate back to
this same workspace's REST APIs - there is no cluster-local `dbutils` session these
scripts use. `DATABRICKS_HOST` is not sensitive and is set as a literal
`spark_env_vars` value below; `DATABRICKS_TOKEN` is templated as
`{{secrets/fuelsignal/token}}` and requires a Databricks secret to actually exist:

    databricks secrets create-scope fuelsignal
    databricks secrets put-secret fuelsignal token   # paste a long-lived PAT

A dedicated long-lived PAT for job execution was NOT created by this script or by
the agent that wrote it - credential creation was deliberately left for a human to
do explicitly, rather than an agent minting new standing credentials on a live
account unattended. Every job below is created with its schedule PAUSED for the
same reason - see docs/jobs-and-scheduling.md for the full rationale and the exact
activation steps once the secret exists.

Idempotent: re-running this script updates existing jobs (matched by name) in place
via `jobs/reset` rather than creating duplicates.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


def _find_project_root() -> Path:
    """Walk up from the current directory looking for pyproject.toml - robust to
    whatever directory Databricks' git_source spark_python_task execution happens
    to set as cwd (live-verified 2026-07-18: it's the script's own containing
    directory, e.g. .../scripts, not the repo root - Path.cwd() alone is wrong)."""
    candidate = Path.cwd()
    for _ in range(5):
        if (candidate / "pyproject.toml").exists():
            return candidate
        candidate = candidate.parent
    return Path.cwd()


try:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
except NameError:
    # __file__ is undefined under Databricks git_source exec-style execution.
    PROJECT_ROOT = _find_project_root()
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from run_ingestion_pipeline import databricks_auth  # noqa: E402

from fuelsignal.config import load_env  # noqa: E402

GIT_SOURCE = {
    "git_url": "https://github.com/ozzy2438/fuelsignal-retail-fuel-pricing-platform.git",
    "git_provider": "gitHub",
    "git_branch": "main",
}

BASE_DEPS = [
    "requests>=2.31.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "pandas>=2.0.0",
    "numpy>=1.24.0",
    "openpyxl>=3.1.0",
    "lxml>=5.0.0",
    # databricks.sdk.runtime.dbutils is the fallback credential channel
    # (databricks_auth's _dbutils_secret) - usually preinstalled in the Databricks
    # Runtime image, pinned explicitly here so the job environment doesn't depend
    # on that assumption.
    "databricks-sdk>=0.20.0",
]
ML_DEPS = [*BASE_DEPS, "lightgbm>=4.0.0", "scikit-learn>=1.3.0", "mlflow>=2.10.0"]

TIMEZONE = "Australia/Sydney"


def _credential_parameters(host: str) -> list[str]:
    # spark_env_vars does NOT reliably reach the process environment for a
    # serverless spark_python_task (live-verified 2026-07-18: DATABRICKS_HOST/TOKEN
    # were empty inside the task despite being set here) - job parameters are the
    # channel that actually works, and are the documented way to substitute a
    # `{{secrets/scope/key}}` reference into a task at run time. Every script's
    # databricks_auth() (run_ingestion_pipeline.py) reads these two flags from
    # sys.argv before falling back to environment variables / CLI OAuth.
    return [
        "--databricks-host",
        host,
        "--databricks-token",
        "{{secrets/fuelsignal/token}}",
    ]


def _task(
    task_key: str, python_file: str, deps: list[str], host: str, depends_on: str | None = None
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "task_key": task_key,
        # source: GIT is required for git_source-based file tasks - without it the
        # platform looks for python_file in the workspace filesystem instead of the
        # git checkout and fails with "Cannot read the python file" (live-verified
        # 2026-07-18). Without it, __file__ is also undefined inside the script (the
        # file runs through an exec-style context, not a plain `python file.py`
        # invocation) - every script's PROJECT_ROOT falls back to Path.cwd() to
        # handle that; see the try/except at the top of each script.
        "spark_python_task": {
            "python_file": python_file,
            "source": "GIT",
            "parameters": _credential_parameters(host),
        },
        "environment_key": task_key,
    }
    if depends_on:
        task["depends_on"] = [{"task_key": depends_on}]
    return task


def _environment(task_key: str, deps: list[str]) -> dict[str, Any]:
    # client "1" fails on this workspace ("Invalid platform channel Client-1",
    # live-verified 2026-07-18 - the cluster never launches); "2" works.
    return {"environment_key": task_key, "spec": {"client": "2", "dependencies": deps}}


def build_job_definitions(host: str) -> list[dict[str, Any]]:
    daily_pipeline_tasks = [
        _task("ingest_sources", "/scripts/run_ingestion_pipeline.py", BASE_DEPS, host),
        _task(
            "refresh_gold",
            "/scripts/run_gold_pipeline.py",
            BASE_DEPS,
            host,
            depends_on="ingest_sources",
        ),
        _task(
            "score_jump_and_forecast",
            "/scripts/score_daily.py",
            ML_DEPS,
            host,
            depends_on="refresh_gold",
        ),
    ]
    monitoring_tasks = [
        _task("validate_pipeline", "/scripts/validate_live_pipeline.py", BASE_DEPS, host),
    ]

    return [
        {
            "name": "fuelsignal-daily-pipeline",
            "tasks": daily_pipeline_tasks,
            "environments": [
                _environment("ingest_sources", BASE_DEPS),
                _environment("refresh_gold", BASE_DEPS),
                _environment("score_jump_and_forecast", ML_DEPS),
            ],
            "git_source": GIT_SOURCE,
            "schedule": {
                "quartz_cron_expression": "0 0 5 * * ?",
                "timezone_id": TIMEZONE,
                "pause_status": "PAUSED",
            },
            "max_concurrent_runs": 1,
            "timeout_seconds": 3600,
        },
        {
            "name": "fuelsignal-monitoring-checks",
            "tasks": monitoring_tasks,
            "environments": [_environment("validate_pipeline", BASE_DEPS)],
            "git_source": GIT_SOURCE,
            "schedule": {
                "quartz_cron_expression": "0 30 5 * * ?",
                "timezone_id": TIMEZONE,
                "pause_status": "PAUSED",
            },
            "max_concurrent_runs": 1,
            "timeout_seconds": 900,
        },
    ]


def deploy_job(host: str, headers: dict[str, str], job_def: dict[str, Any]) -> dict[str, Any]:
    list_resp = requests.get(f"{host}/api/2.1/jobs/list", headers=headers, timeout=30)
    list_resp.raise_for_status()
    existing = {job["settings"]["name"]: job["job_id"] for job in list_resp.json().get("jobs", [])}

    if job_def["name"] in existing:
        job_id = existing[job_def["name"]]
        resp = requests.post(
            f"{host}/api/2.1/jobs/reset",
            headers=headers,
            json={"job_id": job_id, "new_settings": job_def},
            timeout=30,
        )
        resp.raise_for_status()
        return {"job_id": job_id, "name": job_def["name"], "action": "updated"}

    resp = requests.post(f"{host}/api/2.1/jobs/create", headers=headers, json=job_def, timeout=30)
    resp.raise_for_status()
    return {"job_id": resp.json()["job_id"], "name": job_def["name"], "action": "created"}


def main() -> int:
    load_env()
    host, token = databricks_auth()
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_TOKEN"] = token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    results = [deploy_job(host, headers, job_def) for job_def in build_job_definitions(host)]

    print(
        json.dumps(
            {"jobs": results, "schedules": "all PAUSED - see docs/jobs-and-scheduling.md"}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code != 0:
        # Databricks' git_source spark_python_task execution (an exec-style,
        # non-notebook context) treats *any* raised SystemExit - even SystemExit(0)
        # - as a task failure (live-verified 2026-07-18: a script that printed a
        # full success summary and returned 0 was still marked FAILED). Only raise
        # on a genuine non-zero exit code; falling through on success matches plain
        # `python script.py`'s exit-0 behavior for local/CI execution too.
        raise SystemExit(_exit_code)
