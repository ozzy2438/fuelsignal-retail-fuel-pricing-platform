"""Daily jump-scoring, forecasting, and pricing-policy job (Week 2 Phase 5-6).

The lightweight, daily-operation counterpart to scripts/run_pricing_policy_backtest.py:
scores only the latest available market_date's eligible station-fuel rows, using the
SAME reused (never retrained) Phase 2 jump classifier and the most recently logged
Phase 4/5 forecast models - looked up live from MLflow, not hardcoded. Where the
backtest fully refreshes six months of history, this job *upserts* a single day
(delete WHERE market_date = that day, then insert) so it is cheap enough to run
daily without re-scoring the whole archive.

Phase 6: both Gold pulls (`fetch_training_data`, `fetch_market_price_series`) are
now bounded to a trailing `LOOKBACK_DAYS` window ending at the latest available
market_date, found first via one cheap `MAX(market_date)` query - not the full
multi-year archive Phase 5 originally pulled just to score one day. 60 days is
generous headroom over both real needs: the 14-day rolling window
`build_feature_frame` computes, and the empirical 5-8.5 day inter-jump cycle length
that `days_since_price_change`/`days_since_last_jump` are sized against. A station
whose actual last price change was more than 60 days before score_date will have its
`days_since_price_change` under-counted (measured from the start of the window, not
the true last change) - accepted as a rare-case approximation: 60 days already
dwarfs the `stale_price_days_threshold` (7), so the staleness flag still fires
correctly in practice even when the exact count is off. See
docs/jobs-and-scheduling.md for the full trade-off note.

Intended as the "jump scoring" + "3-day/7-day forecasting" step in the daily
Databricks Jobs pipeline - see docs/jobs-and-scheduling.md. Uses the identical
decide_policy() rule and recommendation_status safety gate as the backtest; no
policy logic is duplicated or reimplemented here.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import mlflow
import yaml
from mlflow.tracking import MlflowClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from deploy_databricks_foundation import DatabricksSqlClient, DeploymentError  # noqa: E402
from forecast_prices import build_feature_frame, fetch_market_price_series  # noqa: E402
from run_ingestion_pipeline import databricks_auth, git_commit_short  # noqa: E402
from run_pricing_policy_backtest import (  # noqa: E402
    RECOMMENDATION_COLUMNS,
    RECOMMENDATIONS_TABLE,
    build_recommendations,
    deploy_dashboard_schema,
    find_phase2_jump_model,
    insert_rows,
    score_forecast,
)
from train_jump_model import CATEGORICAL_COLUMNS as JUMP_CATEGORICAL_COLUMNS  # noqa: E402
from train_jump_model import FEATURE_COLUMNS as JUMP_FEATURE_COLUMNS  # noqa: E402
from train_jump_model import GOLD_SCHEMA as JUMP_GOLD_SCHEMA  # noqa: E402
from train_jump_model import fetch_training_data  # noqa: E402

from fuelsignal.config import load_env, load_project_config  # noqa: E402
from fuelsignal.policy.backtest_metrics import days_since_price_change_series  # noqa: E402
from fuelsignal.policy.pricing_policy import PolicyParams  # noqa: E402

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"
LOOKBACK_DAYS = 60


def fetch_latest_eligible_market_date(client: DatabricksSqlClient, fuel_types: list[str]) -> date:
    """Cheap scalar MAX(market_date) query - lets the real pulls below be bounded to
    a trailing window instead of scanning the full archive just to discover the
    latest date."""
    fuel_list = ", ".join(f"'{f}'" for f in fuel_types)
    result = client.execute(
        f"""
        SELECT MAX(f.market_date)
        FROM {JUMP_GOLD_SCHEMA}.gold_daily_pricing_inputs f
        JOIN {JUMP_GOLD_SCHEMA}.gold_model_eligibility e
          ON f.station_id = e.station_id AND f.fuel_type = e.fuel_type AND e.is_eligible
        WHERE f.fuel_type IN ({fuel_list})
        """
    )
    value = result["result"]["data_array"][0][0]
    if value is None:
        raise RuntimeError("No eligible rows found in gold_daily_pricing_inputs")
    return date.fromisoformat(value)


def find_latest_forecast_models(mlflow_client: MlflowClient) -> tuple[dict[int, object], str]:
    """Most recent finished pricing-policy run's logged 3-day/7-day forecast models
    - never refit here, matching the "reuse, don't retrain" rule."""
    experiment = mlflow_client.get_experiment_by_name("/Shared/fuelsignal-pricing-policy")
    runs = mlflow_client.search_runs(
        [experiment.experiment_id],
        filter_string="status = 'FINISHED'",
        order_by=["start_time DESC"],
        max_results=1,
    )
    if not runs:
        raise RuntimeError("No finished pricing-policy run with logged forecast models found")
    run = runs[0]
    models: dict[int, object] = {}
    for out in run.outputs.model_outputs:
        logged_model = mlflow_client.get_logged_model(out.model_id)
        name = logged_model.name
        if name == "forecast_model_h3":
            models[3] = mlflow.lightgbm.load_model(f"models:/{out.model_id}")
        elif name == "forecast_model_h7":
            models[7] = mlflow.lightgbm.load_model(f"models:/{out.model_id}")
    if 3 not in models or 7 not in models:
        raise RuntimeError(f"Run {run.info.run_id} is missing a forecast_model_h3/h7 artifact")
    return models, run.info.run_id


def main() -> int:
    load_env()
    host, token = databricks_auth()
    client = DatabricksSqlClient(host=host, token=token)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_TOKEN"] = token
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment("/Shared/fuelsignal-pricing-policy")
    mlflow_client = MlflowClient()

    with open(PROJECT_ROOT / "config" / "pricing_policy.yml") as f:
        policy_config = yaml.safe_load(f)["pricing_policy"]
    with open(PROJECT_ROOT / "config" / "model_thresholds.yml") as f:
        threshold_config = yaml.safe_load(f)
    thresholds = {
        fuel: entry["threshold"] for fuel, entry in threshold_config["thresholds"].items()
    }
    project_config = load_project_config()
    forecast_config = project_config["price_forecast"]

    automated = set(policy_config["automated_fuel_types"])
    watch_only = set(policy_config["watch_only_fuel_types"])
    all_fuel_types = sorted(automated | watch_only)
    params = PolicyParams(
        lead_min_forecast_change_cpl=policy_config["lead_min_forecast_change_cpl"],
        lead_step_cpl=policy_config["lead_step_cpl"],
        follow_min_overpriced_cpl=policy_config["follow_min_overpriced_cpl"],
        follow_forecast_decline_cpl=policy_config["follow_forecast_decline_cpl"],
        min_margin_guardrail_cpl=policy_config["min_margin_guardrail_cpl"],
    )
    run_id = f"score-daily-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    ingested_at = datetime.now(timezone.utc)

    try:
        deploy_dashboard_schema(client)

        print("Locating reused jump classifier and forecast models...", file=sys.stderr)
        jump_model_id, jump_source_run_id, model_train_end = find_phase2_jump_model(mlflow_client)
        jump_model = mlflow.lightgbm.load_model(f"models:/{jump_model_id}")
        forecast_models, forecast_source_run_id = find_latest_forecast_models(mlflow_client)

        print("Finding the latest eligible market_date...", file=sys.stderr)
        score_date = fetch_latest_eligible_market_date(client, all_fuel_types)
        if score_date <= model_train_end:
            raise RuntimeError(
                f"Latest available market_date ({score_date}) is not after the jump "
                f"classifier's train cutoff ({model_train_end}) - nothing new to score."
            )
        since_date = score_date - timedelta(days=LOOKBACK_DAYS)
        print(f"Scoring market_date={score_date} (pulling since {since_date})", file=sys.stderr)

        print("Pulling jump-model feature data (bounded trailing window)...", file=sys.stderr)
        jump_df = fetch_training_data(client, all_fuel_types, since_date=since_date)
        jump_df["market_date"] = jump_df["market_date"].dt.date
        jump_df["jump_within_48h"] = jump_df["jump_within_48h"].astype(bool)
        print(f"  {len(jump_df)} rows", file=sys.stderr)

        print("Pulling market-level price series (bounded trailing window)...", file=sys.stderr)
        market_raw = fetch_market_price_series(
            client, forecast_config["included_fuel_types"], since_date=since_date
        )
        market_features = build_feature_frame(
            market_raw, forecast_config["rolling_windows_days"], [3, 7]
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

        jump_df = jump_df.sort_values(["station_id", "fuel_type", "market_date"])
        jump_df["days_since_price_change"] = jump_df.groupby(["station_id", "fuel_type"])[
            "daily_close_price_cpl"
        ].transform(lambda s: days_since_price_change_series(list(s)))

        merged = jump_df.merge(forecast_lookup, on=["fuel_type", "market_date"], how="left")
        todays_rows = merged[merged["market_date"] == score_date].copy()
        print(f"Rows to score for {score_date}: {len(todays_rows)}", file=sys.stderr)

        recommendations = build_recommendations(
            todays_rows,
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

        print(f"Upserting {len(recommendations)} rows for {score_date}...", file=sys.stderr)
        client.execute(
            f"DELETE FROM {RECOMMENDATIONS_TABLE} WHERE market_date = DATE'{score_date}'"
        )
        insert_rows(
            client,
            RECOMMENDATIONS_TABLE,
            RECOMMENDATION_COLUMNS,
            recommendations[RECOMMENDATION_COLUMNS].to_dict("records"),
            chunk_size=2000,
        )

        with mlflow.start_run(run_name=run_id):
            mlflow.log_params(
                {
                    "score_date": str(score_date),
                    "jump_model_id": jump_model_id,
                    "jump_model_source_run_id": jump_source_run_id,
                    "forecast_source_run_id": forecast_source_run_id,
                    "code_version": git_commit_short(),
                }
            )
            mlflow.set_tags({"phase": "week2-phase6-daily-scoring"})
            mlflow.log_metrics(
                {
                    "rows_scored": len(recommendations),
                    "automated_count": int(
                        (recommendations["recommendation_status"] == "automated").sum()
                    ),
                    "watch_only_count": int(
                        (recommendations["recommendation_status"] == "watch_only").sum()
                    ),
                    "disabled_unsafe_count": int(
                        (recommendations["recommendation_status"] == "disabled_unsafe").sum()
                    ),
                }
            )

        output = {
            "run_id": run_id,
            "score_date": str(score_date),
            "rows_scored": len(recommendations),
        }
        print(json.dumps(output, indent=2, default=str))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Daily scoring failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
