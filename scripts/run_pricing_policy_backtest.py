"""HOLD / FOLLOW / LEAD pricing-policy engine and six-month backtest (Week 2 Phase 4-5).

Phase 5 (operationalisation, 2026-07-18) adds a three-way `recommendation_status`
safety gate (automated/watch_only/disabled_unsafe - see
src/fuelsignal/policy/pricing_policy.py), re-tunes the TGP margin guardrail floor
from 1.0 to 2.0 cpl (docs/pricing-policy.md SS8), and deploys the dashboard-ready
views and the monitoring_fuel_policy_status reference table
(deploy_dashboard_schema). Every re-run fully refreshes
monitoring_pricing_policy_recommendations, monitoring_policy_backtest_summary and
monitoring_fuel_policy_status (delete then insert) - these represent THE current
backtest and policy configuration, not an accumulating multi-version log.

Reuses, without retraining, the Phase 2 LightGBM jump classifier (looked up live from
the `/Shared/fuelsignal-jump-model` MLflow experiment - the most recent
`week2-phase2-modelling` parent run's logged model, trained on
`model_train_start_date`..`model_train_end_date` per config/pricing_policy.yml) and
the calibrated per-fuel thresholds from config/model_thresholds.yml. Phase 3's price
forecast evaluated accuracy but never persisted a deployable model, so this script
fits exactly one 3-day and one 7-day LightGBM regressor - identical, unchanged
features/hyperparameters from scripts/forecast_prices.py, on the same train window as
the jump classifier - and logs them to MLflow so this gap does not recur. Neither fit
is a retrain of validated methodology; both are one-time artifacts of an already
-validated approach.

The policy itself (src/fuelsignal/policy/pricing_policy.py) is applied independently
to every eligible station-fuel-day in the backtest window
(config/pricing_policy.yml -> backtest_start_date..backtest_end_date, the entire
out-of-sample span after the jump classifier's train cutoff) - a leakage-safe,
per-day recommendation generation, not a compounding price-trajectory simulation.
Row-level recommendations are written to
monitoring_pricing_policy_recommendations; the aggregate comparison against an
always-HOLD baseline policy is written to monitoring_policy_backtest_summary.

Does not claim revenue or profit - sales-volume data is unavailable, so every margin
number here is indicative (retail price minus TGP), never a realised P&L figure.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import yaml
from mlflow.tracking import MlflowClient

try:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
except NameError:
    # Databricks git_source spark_python_task executes via an exec-style context
    # where __file__ is undefined - the working directory is the repo checkout root.
    PROJECT_ROOT = Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from deploy_databricks_foundation import DatabricksSqlClient, DeploymentError  # noqa: E402
from forecast_prices import CATEGORICAL_COLUMNS as FORECAST_CATEGORICAL_COLUMNS  # noqa: E402
from forecast_prices import FEATURE_COLUMNS as FORECAST_FEATURE_COLUMNS  # noqa: E402
from forecast_prices import (  # noqa: E402
    build_feature_frame,
    fetch_market_price_series,
    train_lightgbm_regressor,
)
from run_ingestion_pipeline import databricks_auth, git_commit_short, sql_literal  # noqa: E402
from train_jump_model import CATEGORICAL_COLUMNS as JUMP_CATEGORICAL_COLUMNS  # noqa: E402
from train_jump_model import FEATURE_COLUMNS as JUMP_FEATURE_COLUMNS  # noqa: E402
from train_jump_model import fetch_training_data  # noqa: E402

from fuelsignal.config import load_env, load_project_config  # noqa: E402
from fuelsignal.monitoring import get_dashboard_view_ddl, get_monitoring_ddl  # noqa: E402
from fuelsignal.policy.backtest_metrics import (  # noqa: E402
    days_since_price_change_series,
    is_priced_above_competitors,
    is_stale,
    summarize,
)
from fuelsignal.policy.pricing_policy import (  # noqa: E402
    PolicyInputs,
    PolicyParams,
    decide_policy,
)

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"
GOLD_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_gold"
MONITORING_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_monitoring"
RECOMMENDATIONS_TABLE = f"{MONITORING_SCHEMA}.monitoring_pricing_policy_recommendations"
SUMMARY_TABLE = f"{MONITORING_SCHEMA}.monitoring_policy_backtest_summary"
RESULTS_PATH = PROJECT_ROOT / "config" / "pricing_policy_backtest_results.json"

RECOMMENDATION_COLUMNS = [
    "station_id",
    "fuel_type",
    "market_date",
    "policy_mode",
    "action",
    "recommendation_status",
    "reason",
    "guardrail_triggered",
    "jump_signal_used",
    "forecast_signal_used",
    "jump_probability",
    "jump_threshold",
    "forecast_3d_change_cpl",
    "forecast_7d_change_cpl",
    "station_vs_competitor_median_cpl",
    "current_price_cpl",
    "tgp_cpl",
    "actual_indicative_margin_cpl",
    "hypothetical_price_cpl",
    "hypothetical_margin_cpl",
    "margin_difference_cpl",
    "days_since_price_change",
    "is_stale_actual",
    "priced_above_competitors_actual",
    "baseline_action",
    "code_version",
    "backtest_run_id",
    "_pipeline_run_id",
    "ingested_at",
]

SUMMARY_COLUMNS = [
    "backtest_run_id",
    "fuel_type",
    "policy_mode",
    "hold_count",
    "follow_count",
    "lead_count",
    "baseline_hold_count",
    "guardrail_intervention_count",
    "stale_price_days_policy",
    "stale_price_days_baseline",
    "days_priced_above_competitors_actual",
    "days_priced_above_competitors_unaddressed",
    "automated_status_count",
    "watch_only_status_count",
    "disabled_unsafe_status_count",
    "avg_margin_difference_cpl",
    "total_margin_difference_cpl",
    "jump_signal_contribution_count",
    "forecast_signal_contribution_count",
    "row_count",
    "backtest_start_date",
    "backtest_end_date",
    "model_train_end_date",
    "generated_at",
    "_pipeline_run_id",
]

FUEL_POLICY_STATUS_TABLE = f"{MONITORING_SCHEMA}.monitoring_fuel_policy_status"
FUEL_POLICY_STATUS_COLUMNS = [
    "fuel_type",
    "jump_model_eligible",
    "calibrated_threshold",
    "tgp_margin_guardrail_valid",
    "lead_enabled",
    "follow_automation_status",
    "policy_notes",
    "effective_date",
    "code_version",
    "_pipeline_run_id",
]

# Existing tables predate the recommendation_status / status-count columns (Phase 4
# shipped without them) - CREATE TABLE IF NOT EXISTS is a no-op against an
# already-existing table, so these columns need an explicit one-time migration.
SCHEMA_MIGRATIONS = [
    (
        f"{MONITORING_SCHEMA}.monitoring_pricing_policy_recommendations",
        "recommendation_status",
        "STRING",
    ),
    (f"{MONITORING_SCHEMA}.monitoring_policy_backtest_summary", "automated_status_count", "LONG"),
    (
        f"{MONITORING_SCHEMA}.monitoring_policy_backtest_summary",
        "watch_only_status_count",
        "LONG",
    ),
    (
        f"{MONITORING_SCHEMA}.monitoring_policy_backtest_summary",
        "disabled_unsafe_status_count",
        "LONG",
    ),
]


def deploy_dashboard_schema(client: DatabricksSqlClient) -> None:
    """Idempotently bring the monitoring schema up to date: migrate the two Phase 4
    tables to add the Phase 5 status columns, create the new
    monitoring_fuel_policy_status table if missing, and (re)create the four
    dashboard views so they always reflect the latest column set."""
    for table, column, col_type in SCHEMA_MIGRATIONS:
        existing = client.execute(f"DESCRIBE TABLE {table}")
        existing_columns = {row[0] for row in existing["result"]["data_array"]}
        if column not in existing_columns:
            client.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            print(f"  migrated {table}: added {column} {col_type}", file=sys.stderr)

    fuel_policy_status_ddl = get_monitoring_ddl(MONITORING_SCHEMA)["monitoring_fuel_policy_status"]
    client.execute(fuel_policy_status_ddl)

    silver_schema = f"{CATALOG}.{SCHEMA_PREFIX}_silver"
    for view_name, ddl in get_dashboard_view_ddl(MONITORING_SCHEMA, silver_schema).items():
        client.execute(ddl)
        print(f"  deployed view {view_name}", file=sys.stderr)


def find_phase2_jump_model(mlflow_client: MlflowClient) -> tuple[str, str, date]:
    """Locate the most recent Phase 2 `week2-phase2-modelling` run's logged
    LightGBM classifier and the exact date it was trained through (the last
    fold's train_end) - looked up live, never hardcoded, so this keeps working if
    Phase 2 is ever legitimately re-run."""
    experiment = mlflow_client.get_experiment_by_name("/Shared/fuelsignal-jump-model")
    parent_runs = mlflow_client.search_runs(
        [experiment.experiment_id],
        filter_string="tags.phase = 'week2-phase2-modelling' and status = 'FINISHED'",
        order_by=["start_time DESC"],
        max_results=5,
    )
    if not parent_runs:
        raise RuntimeError("No finished week2-phase2-modelling run found in MLflow")
    parent = parent_runs[0]

    fold_runs = mlflow_client.search_runs(
        [experiment.experiment_id],
        filter_string=f"tags.mlflow.parentRunId = '{parent.info.run_id}'",
        order_by=["params.fold_index DESC"],
        max_results=1,
    )
    if not fold_runs:
        raise RuntimeError(f"No fold runs found under parent run {parent.info.run_id}")
    train_end = date.fromisoformat(fold_runs[0].data.params["train_end"])

    logged_model_ids = [out.model_id for out in parent.outputs.model_outputs]
    for model_id in logged_model_ids:
        logged_model = mlflow_client.get_logged_model(model_id)
        if logged_model.tags.get("mlflow.source.name") == "scripts/train_jump_model.py":
            return model_id, parent.info.run_id, train_end
    raise RuntimeError(f"No model on run {parent.info.run_id} sourced from train_jump_model.py")


def fit_forecast_models(
    market_features: pd.DataFrame, train_start: date, train_end: date, horizons: list[int]
) -> dict[int, Any]:
    """One fit per horizon on train_start..train_end, matching Phase 3's exact
    feature set and hyperparameters (forecast_prices.train_lightgbm_regressor) -
    Phase 3 evaluated accuracy but never persisted a model, so this is the first
    time these are logged."""
    models: dict[int, Any] = {}
    train_mask = (market_features["market_date"] >= train_start) & (
        market_features["market_date"] <= train_end
    )
    for horizon in horizons:
        target_col = f"target_price_h{horizon}"
        train_rows = market_features[train_mask].dropna(subset=[target_col])
        x_train = train_rows[FORECAST_FEATURE_COLUMNS + FORECAST_CATEGORICAL_COLUMNS].copy()
        x_train["fuel_type"] = x_train["fuel_type"].astype("category")
        y_train = train_rows[target_col]
        models[horizon] = train_lightgbm_regressor(x_train, y_train)
    return models


def score_forecast(model: Any, market_features: pd.DataFrame) -> pd.Series:
    x = market_features[FORECAST_FEATURE_COLUMNS + FORECAST_CATEGORICAL_COLUMNS].copy()
    x["fuel_type"] = x["fuel_type"].astype("category")
    return pd.Series(model.predict(x), index=market_features.index)


def _sql_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "NULL"
    if isinstance(value, bool | np.bool_):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | np.integer):
        return str(int(value))
    if isinstance(value, float | np.floating):
        return repr(float(value))
    if isinstance(value, datetime):
        return f"TIMESTAMP'{value.isoformat()}'"
    if isinstance(value, date):
        return f"DATE'{value.isoformat()}'"
    return sql_literal(str(value))


def insert_rows(
    client: DatabricksSqlClient,
    table: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    chunk_size: int,
) -> None:
    total = len(rows)
    for start in range(0, total, chunk_size):
        chunk = rows[start : start + chunk_size]
        values_sql = ",\n".join(
            "(" + ", ".join(_sql_value(row[c]) for c in columns) + ")" for row in chunk
        )
        client.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values_sql}")
        print(f"  inserted {min(start + chunk_size, total)}/{total} into {table}", file=sys.stderr)


def build_recommendations(
    frame: pd.DataFrame,
    params: PolicyParams,
    automated_fuel_types: set[str],
    thresholds: dict[str, float],
    stale_days_threshold: float,
    follow_min_overpriced_cpl: float,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        automation_enabled = row.fuel_type in automated_fuel_types
        inputs = PolicyInputs(
            fuel_type=row.fuel_type,
            automation_enabled=automation_enabled,
            current_price_cpl=_none_if_nan(row.daily_close_price_cpl),
            jump_probability=_none_if_nan(row.jump_probability),
            jump_threshold=thresholds.get(row.fuel_type, 0.5),
            forecast_3d_change_cpl=_none_if_nan(row.forecast_3d_change_cpl),
            station_vs_competitor_median_cpl=_none_if_nan(row.station_vs_competitor_median_cpl),
            tgp_cpl=_none_if_nan(row.tgp_cpl),
            indicative_margin_cpl=_none_if_nan(row.indicative_margin_cpl),
        )
        decision = decide_policy(inputs, params)
        actual_margin = _none_if_nan(row.indicative_margin_cpl)
        margin_difference = (
            decision.hypothetical_margin_cpl - actual_margin
            if decision.hypothetical_margin_cpl is not None and actual_margin is not None
            else None
        )
        stale_actual = is_stale(int(row.days_since_price_change), stale_days_threshold)
        above_competitors_actual = is_priced_above_competitors(
            _none_if_nan(row.station_vs_competitor_median_cpl), follow_min_overpriced_cpl
        )
        records.append(
            {
                "station_id": row.station_id,
                "fuel_type": row.fuel_type,
                "market_date": row.market_date,
                "policy_mode": decision.mode,
                "action": decision.action,
                "recommendation_status": decision.recommendation_status,
                "reason": decision.reason,
                "guardrail_triggered": decision.guardrail_triggered,
                "jump_signal_used": decision.jump_signal_used,
                "forecast_signal_used": decision.forecast_signal_used,
                "jump_probability": inputs.jump_probability,
                "jump_threshold": inputs.jump_threshold,
                "forecast_3d_change_cpl": inputs.forecast_3d_change_cpl,
                "forecast_7d_change_cpl": _none_if_nan(row.forecast_7d_change_cpl),
                "station_vs_competitor_median_cpl": inputs.station_vs_competitor_median_cpl,
                "current_price_cpl": inputs.current_price_cpl,
                "tgp_cpl": inputs.tgp_cpl,
                "actual_indicative_margin_cpl": actual_margin,
                "hypothetical_price_cpl": decision.hypothetical_price_cpl,
                "hypothetical_margin_cpl": decision.hypothetical_margin_cpl,
                "margin_difference_cpl": margin_difference,
                "days_since_price_change": int(row.days_since_price_change),
                "is_stale_actual": stale_actual,
                "priced_above_competitors_actual": above_competitors_actual,
                "baseline_action": "HOLD",
                "jump_within_48h": bool(row.jump_within_48h),
            }
        )
    return pd.DataFrame.from_records(records)


def _none_if_nan(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def main() -> int:
    load_env()
    host, token = databricks_auth()
    client = DatabricksSqlClient(host=host, token=token)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_TOKEN"] = token
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment("/Shared/fuelsignal-pricing-policy")
    mlflow_client = MlflowClient()

    print("Deploying/migrating dashboard schema (idempotent)...", file=sys.stderr)
    deploy_dashboard_schema(client)

    project_config = load_project_config()
    forecast_config = project_config["price_forecast"]
    with open(PROJECT_ROOT / "config" / "pricing_policy.yml") as f:
        policy_config = yaml.safe_load(f)["pricing_policy"]
    with open(PROJECT_ROOT / "config" / "model_thresholds.yml") as f:
        threshold_config = yaml.safe_load(f)
    thresholds = {
        fuel: entry["threshold"] for fuel, entry in threshold_config["thresholds"].items()
    }

    automated = set(policy_config["automated_fuel_types"])
    watch_only = set(policy_config["watch_only_fuel_types"])
    all_fuel_types = sorted(automated | watch_only)
    backtest_start = date.fromisoformat(policy_config["backtest_start_date"])
    backtest_end = date.fromisoformat(policy_config["backtest_end_date"])
    model_train_start = date.fromisoformat(policy_config["model_train_start_date"])

    params = PolicyParams(
        lead_min_forecast_change_cpl=policy_config["lead_min_forecast_change_cpl"],
        lead_step_cpl=policy_config["lead_step_cpl"],
        follow_min_overpriced_cpl=policy_config["follow_min_overpriced_cpl"],
        follow_forecast_decline_cpl=policy_config["follow_forecast_decline_cpl"],
        min_margin_guardrail_cpl=policy_config["min_margin_guardrail_cpl"],
    )
    run_id = f"policy-backtest-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    ingested_at = datetime.now(timezone.utc)

    try:
        print("Locating Phase 2 jump classifier...", file=sys.stderr)
        model_id, source_run_id, model_train_end = find_phase2_jump_model(mlflow_client)
        if model_train_end != date.fromisoformat(policy_config["model_train_end_date"]):
            raise RuntimeError(
                f"Live jump-model train_end ({model_train_end}) does not match "
                f"config/pricing_policy.yml model_train_end_date "
                f"({policy_config['model_train_end_date']}) - config is stale."
            )
        jump_model = mlflow.lightgbm.load_model(f"models:/{model_id}")
        print(
            f"  loaded model_id={model_id} from run={source_run_id}, train_end={model_train_end}",
            file=sys.stderr,
        )

        print(
            "Pulling jump-model feature data (all eligible station-fuel-days)...", file=sys.stderr
        )
        jump_df = fetch_training_data(client, all_fuel_types)
        jump_df["market_date"] = jump_df["market_date"].dt.date
        jump_df["jump_within_48h"] = jump_df["jump_within_48h"].astype(bool)
        print(f"  {len(jump_df)} rows", file=sys.stderr)

        print("Pulling market-level price series for the forecast...", file=sys.stderr)
        market_raw = fetch_market_price_series(client, forecast_config["included_fuel_types"])
        market_features = build_feature_frame(
            market_raw, forecast_config["rolling_windows_days"], [3, 7]
        )

        print(
            "Fitting the 3-day and 7-day forecast models (one-time, unchanged Phase 3 methodology)...",
            file=sys.stderr,
        )
        forecast_models = fit_forecast_models(
            market_features, model_train_start, model_train_end, [3, 7]
        )
        market_features["pred_h3"] = score_forecast(forecast_models[3], market_features)
        market_features["pred_h7"] = score_forecast(forecast_models[7], market_features)
        market_features["forecast_3d_change_cpl"] = (
            market_features["pred_h3"] - market_features["market_median_price_cpl"]
        )
        market_features["forecast_7d_change_cpl"] = (
            market_features["pred_h7"] - market_features["market_median_price_cpl"]
        )
        forecast_lookup = market_features[
            ["fuel_type", "market_date", "forecast_3d_change_cpl", "forecast_7d_change_cpl"]
        ]

        print("Scoring jump probabilities...", file=sys.stderr)
        x_jump = jump_df[JUMP_FEATURE_COLUMNS + JUMP_CATEGORICAL_COLUMNS].copy()
        x_jump["fuel_type"] = x_jump["fuel_type"].astype("category")
        jump_df["jump_probability"] = jump_model.predict_proba(x_jump)[:, 1]

        print("Computing days-since-price-change per station x fuel type...", file=sys.stderr)
        jump_df = jump_df.sort_values(["station_id", "fuel_type", "market_date"])
        jump_df["days_since_price_change"] = jump_df.groupby(["station_id", "fuel_type"])[
            "daily_close_price_cpl"
        ].transform(lambda s: days_since_price_change_series(list(s)))

        print("Merging market-level forecast into station-level rows...", file=sys.stderr)
        merged = jump_df.merge(forecast_lookup, on=["fuel_type", "market_date"], how="left")

        backtest = merged[
            (merged["market_date"] >= backtest_start) & (merged["market_date"] <= backtest_end)
        ].copy()
        print(f"Backtest window rows: {len(backtest)}", file=sys.stderr)

        print("Applying the policy to every backtest row...", file=sys.stderr)
        recommendations = build_recommendations(
            backtest,
            params,
            automated,
            thresholds,
            policy_config["stale_price_days_threshold"],
            policy_config["follow_min_overpriced_cpl"],
        )
        recommendations["code_version"] = git_commit_short()
        recommendations["backtest_run_id"] = run_id
        recommendations["_pipeline_run_id"] = run_id
        recommendations["ingested_at"] = ingested_at

        # This table represents THE current backtest, not a multi-version audit log -
        # full refresh (delete then insert) rather than accumulating superseded rows
        # from earlier policy versions across re-runs.
        print(f"Clearing prior rows from {RECOMMENDATIONS_TABLE}...", file=sys.stderr)
        client.execute(f"DELETE FROM {RECOMMENDATIONS_TABLE}")

        print(f"Writing {len(recommendations)} rows to {RECOMMENDATIONS_TABLE}...", file=sys.stderr)
        insert_rows(
            client,
            RECOMMENDATIONS_TABLE,
            RECOMMENDATION_COLUMNS,
            recommendations[RECOMMENDATION_COLUMNS].to_dict("records"),
            chunk_size=2000,
        )

        print("Summarizing results by fuel type...", file=sys.stderr)
        summaries = [summarize(recommendations, fuel) for fuel in all_fuel_types]
        summaries.append(summarize(recommendations, None))
        summary_rows = []
        for s in summaries:
            row = dict(s)
            row.pop("lead_hit_rate", None)
            row["policy_mode"] = (
                "automated"
                if row["fuel_type"] in automated
                else ("watch_only" if row["fuel_type"] in watch_only else "mixed")
            )
            row["backtest_run_id"] = run_id
            row["backtest_start_date"] = backtest_start
            row["backtest_end_date"] = backtest_end
            row["model_train_end_date"] = model_train_end
            row["generated_at"] = ingested_at
            row["_pipeline_run_id"] = run_id
            summary_rows.append(row)

        print(f"Clearing prior rows from {SUMMARY_TABLE}...", file=sys.stderr)
        client.execute(f"DELETE FROM {SUMMARY_TABLE}")
        insert_rows(client, SUMMARY_TABLE, SUMMARY_COLUMNS, summary_rows, chunk_size=100)

        print("Writing fuel policy status reference rows...", file=sys.stderr)
        client.execute(f"DELETE FROM {FUEL_POLICY_STATUS_TABLE}")
        summary_by_fuel = {s["fuel_type"]: s for s in summaries if s["fuel_type"] != "ALL"}
        policy_status_rows = []
        for fuel in all_fuel_types:
            jump_eligible = fuel in automated
            tgp_valid = bool(summary_by_fuel[fuel]["margin_data_available"])
            follow_status = (
                "disabled_unsafe"
                if not tgp_valid
                else ("automated" if jump_eligible else "watch_only")
            )
            notes = []
            if not jump_eligible:
                notes.append(
                    "watch-only: jump-model threshold did not clear the Phase 3 business rule"
                )
            if not tgp_valid:
                notes.append(
                    "disabled_unsafe: no validated TGP margin guardrail (TGP data unavailable)"
                )
            policy_status_rows.append(
                {
                    "fuel_type": fuel,
                    "jump_model_eligible": jump_eligible,
                    "calibrated_threshold": thresholds.get(fuel),
                    "tgp_margin_guardrail_valid": tgp_valid,
                    "lead_enabled": jump_eligible,
                    "follow_automation_status": follow_status,
                    "policy_notes": "; ".join(notes) if notes else "fully automated",
                    "effective_date": backtest_end,
                    "code_version": git_commit_short(),
                    "_pipeline_run_id": run_id,
                }
            )
        insert_rows(
            client,
            FUEL_POLICY_STATUS_TABLE,
            FUEL_POLICY_STATUS_COLUMNS,
            policy_status_rows,
            chunk_size=10,
        )

        with mlflow.start_run(run_name=run_id) as parent_run:
            mlflow.log_params(
                {
                    "automated_fuel_types": ",".join(sorted(automated)),
                    "watch_only_fuel_types": ",".join(sorted(watch_only)),
                    "backtest_start_date": str(backtest_start),
                    "backtest_end_date": str(backtest_end),
                    "model_train_end_date": str(model_train_end),
                    "jump_model_id": model_id,
                    "jump_model_source_run_id": source_run_id,
                    "lead_min_forecast_change_cpl": params.lead_min_forecast_change_cpl,
                    "lead_step_cpl": params.lead_step_cpl,
                    "follow_min_overpriced_cpl": params.follow_min_overpriced_cpl,
                    "follow_forecast_decline_cpl": params.follow_forecast_decline_cpl,
                    "min_margin_guardrail_cpl": params.min_margin_guardrail_cpl,
                    "code_version": git_commit_short(),
                }
            )
            mlflow.set_tags({"phase": "week2-phase5-pricing-policy-operationalisation"})
            batched_metrics: dict[str, float] = {}
            for s in summaries:
                prefix = s["fuel_type"]
                for key, value in s.items():
                    if key in ("fuel_type", "policy_mode") or value is None:
                        continue
                    batched_metrics[f"{prefix}_{key}"] = float(value)
            mlflow.log_metrics(batched_metrics)
            mlflow.log_dict({"summaries": summaries}, "pricing_policy_backtest_summary.json")
            mlflow.lightgbm.log_model(forecast_models[3], name="forecast_model_h3")
            mlflow.lightgbm.log_model(forecast_models[7], name="forecast_model_h7")

            row_counts = {
                "recommendations_written": len(recommendations),
                "summary_rows_written": len(summary_rows),
            }
            output = {
                "run_id": run_id,
                "mlflow_run_id": parent_run.info.run_id,
                "jump_model_id": model_id,
                "jump_model_source_run_id": source_run_id,
                "model_train_end_date": str(model_train_end),
                "backtest_start_date": str(backtest_start),
                "backtest_end_date": str(backtest_end),
                "row_counts": row_counts,
                "summaries": summaries,
            }
            with open(RESULTS_PATH, "w") as f:
                json.dump(output, f, indent=2, sort_keys=True, default=str)
            mlflow.log_artifact(str(RESULTS_PATH))

        print(json.dumps(output, indent=2, sort_keys=True, default=str))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Pricing policy backtest failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
