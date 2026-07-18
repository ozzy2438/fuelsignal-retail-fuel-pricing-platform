"""Train and evaluate the 48h price-jump baseline and LightGBM models (Week 2 Phase 2).

Requires scripts/run_model_eligibility.py to have already populated
gold_model_eligibility. Pulls only eligible rows for the six approved fuel types
(config/project.yml -> modelling.included_fuel_types) from gold_daily_pricing_inputs
(features) joined to gold_price_jump_labels (targets), evaluates a transparent
rule-based baseline and a LightGBM classifier with walk-forward (never random)
validation, and logs every fold to the Databricks-hosted MLflow tracking server.

Does not touch pricing policy or make any commercial-impact claim - see
docs/model-results.md for the explicit scope boundary.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score


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

from deploy_databricks_foundation import DatabricksSqlClient, DeploymentError  # noqa: E402
from run_ingestion_pipeline import databricks_auth, git_commit_short, sql_literal  # noqa: E402

from fuelsignal.config import load_env, load_project_config  # noqa: E402
from fuelsignal.modelling.baseline import (  # noqa: E402
    baseline_predict,
    days_since_last_jump_series,
    median_inter_jump_gap_days,
)
from fuelsignal.modelling.evaluation import (  # noqa: E402
    aggregate_to_market_day,
    evaluate_market_day_warnings,
)
from fuelsignal.modelling.walk_forward import build_walk_forward_folds  # noqa: E402

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"
GOLD_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_gold"

FEATURE_COLUMNS = [
    "daily_close_price_cpl",
    "market_median_price_cpl",
    "local_competitor_median_price_cpl",
    "local_competitor_min_price_cpl",
    "local_competitor_max_price_cpl",
    "station_price_percentile",
    "station_vs_competitor_median_cpl",
    "rank_within_local_market",
    "competitor_count",
    "tgp_cpl",
    "indicative_margin_cpl",
    "margin_compression_cpl",
    "rolling_7d_min_price",
    "rolling_7d_max_price",
    "rolling_7d_mean_price",
    "rolling_7d_std_price",
    "rolling_14d_min_price",
    "rolling_14d_max_price",
    "rolling_14d_price_change_cpl",
    "days_since_local_minimum",
    "days_since_last_detected_jump",
    "price_position_within_14d_range",
    "tgp_7d_change_cpl",
    "day_of_week",
    "is_public_holiday",
]
CATEGORICAL_COLUMNS = ["fuel_type"]
TARGET_COLUMN = "jump_within_48h"


def fetch_training_data(
    client: DatabricksSqlClient, fuel_types: list[str], since_date: date | None = None
) -> pd.DataFrame:
    """Pull eligible rows for the approved fuel types: features + the 48h target.

    `since_date`, when given, filters to `f.market_date >= since_date` - used by
    scripts/score_daily.py to pull a bounded trailing window instead of the full
    archive. Every walk-forward-validated caller (train_jump_model.py's own main(),
    calibrate_thresholds.py, run_pricing_policy_backtest.py) needs the complete
    history and leaves this at the default `None`.
    """
    fuel_list = ", ".join(sql_literal(f) for f in fuel_types)
    columns_sql = ",\n              ".join(f"f.{c}" for c in FEATURE_COLUMNS)
    date_filter = f" AND f.market_date >= DATE'{since_date.isoformat()}'" if since_date else ""
    sql = f"""
        SELECT f.station_id, f.fuel_type, f.market_date,
              {columns_sql},
              l.jump_today, l.jump_within_48h
        FROM {GOLD_SCHEMA}.gold_daily_pricing_inputs f
        JOIN {GOLD_SCHEMA}.gold_model_eligibility e
          ON f.station_id = e.station_id AND f.fuel_type = e.fuel_type AND e.is_eligible
        JOIN {GOLD_SCHEMA}.gold_price_jump_labels l
          ON f.fuel_type = l.fuel_type AND f.market_date = l.market_date
        WHERE f.fuel_type IN ({fuel_list}){date_filter}
    """
    return client.execute_to_dataframe(sql)


def fetch_market_series(client: DatabricksSqlClient, fuel_types: list[str]) -> pd.DataFrame:
    """Pull the small market-level (fuel_type x date) jump series for the baseline."""
    fuel_list = ", ".join(sql_literal(f) for f in fuel_types)
    result = client.execute(
        f"""
        SELECT fuel_type, market_date, jump_today
        FROM {GOLD_SCHEMA}.gold_price_jump_labels
        WHERE fuel_type IN ({fuel_list})
        ORDER BY fuel_type, market_date
        """
    )
    rows = result["result"]["data_array"]
    frame = pd.DataFrame(rows, columns=["fuel_type", "market_date", "jump_today"])
    frame["market_date"] = pd.to_datetime(frame["market_date"]).dt.date
    frame["jump_today"] = frame["jump_today"].map(
        {"true": True, "false": False, True: True, False: False}
    )
    return frame


def row_grain_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_proba: np.ndarray
) -> dict[str, float]:
    """Precision/recall/F1/PR-AUC at the station-day-fuel row grain."""
    if y_true.sum() == 0:
        pr_auc = float("nan")
    else:
        pr_auc = float(average_precision_score(y_true, y_proba))
    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "pr_auc": pr_auc,
        "positive_rate_actual": float(y_true.mean()) if len(y_true) else 0.0,
        "positive_rate_predicted": float(y_pred.mean()) if len(y_pred) else 0.0,
        "row_count": int(len(y_true)),
    }


def market_day_metrics(test_df: pd.DataFrame, predicted_col: str, fuel_type: str) -> dict[str, Any]:
    """False-alarm count and average warning lead time, aggregated to one decision
    per (fuel_type, market_date) via majority vote across that day's station rows."""
    fuel_df = test_df[test_df["fuel_type"] == fuel_type]
    daily = (
        fuel_df.groupby("market_date")
        .agg(
            predicted_positive=(
                predicted_col,
                lambda s: aggregate_to_market_day(list(s.astype(bool))),
            ),
            actual_jump=("jump_today", "first"),
        )
        .sort_index()
    )
    result = evaluate_market_day_warnings(
        predicted_positive=list(daily["predicted_positive"]),
        actual_jump_today=list(daily["actual_jump"]),
        max_lead_days=2,
    )
    return {
        "warning_count": result.warning_count,
        "false_alarm_count": result.false_alarm_count,
        "matched_jump_count": result.matched_jump_count,
        "average_lead_time_days": result.average_lead_time_days,
        "market_days_evaluated": int(len(daily)),
    }


def run_baseline(train_market: pd.DataFrame, full_df: pd.DataFrame, fuel_type: str) -> pd.Series:
    """Fit the baseline's only parameter (cycle length) on TRAIN market-days only,
    then apply it to every row (train and test) using the trailing-only
    days-since-last-jump series computed over the full ordered history."""
    fuel_market = train_market[train_market["fuel_type"] == fuel_type].sort_values("market_date")
    cycle_length = median_inter_jump_gap_days(list(fuel_market["jump_today"]))
    if cycle_length is None:
        cycle_length = 14.0  # no observed jumps yet in train - fall back to the
        # configured jump-detection forecast horizon-adjacent default rather than
        # never warning at all.

    full_market = full_df[full_df["fuel_type"] == fuel_type][
        ["market_date", "jump_today"]
    ].drop_duplicates()
    full_market = full_market.sort_values("market_date")
    days_since = days_since_last_jump_series(list(full_market["jump_today"]))
    lookup = dict(zip(full_market["market_date"], days_since, strict=True))

    predictions = full_df.loc[full_df["fuel_type"] == fuel_type, "market_date"].map(
        lambda d: baseline_predict(lookup.get(d), cycle_length)
    )
    return predictions, cycle_length


def train_lightgbm(train_df: pd.DataFrame) -> lgb.LGBMClassifier:
    x_train = train_df[FEATURE_COLUMNS + CATEGORICAL_COLUMNS].copy()
    x_train["fuel_type"] = x_train["fuel_type"].astype("category")
    y_train = train_df[TARGET_COLUMN].astype(int)

    model = lgb.LGBMClassifier(
        objective="binary",
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=31,
        class_weight="balanced",
        random_state=42,
        verbosity=-1,
    )
    model.fit(x_train, y_train, categorical_feature=CATEGORICAL_COLUMNS)
    return model


def main() -> int:
    load_env()
    host, token = databricks_auth()
    client = DatabricksSqlClient(host=host, token=token)
    # Force the freshly resolved credentials (PAT env vars or short-lived CLI OAuth
    # token, per databricks_auth()) - .env may hold a stale DATABRICKS_TOKEN that
    # load_env() already populated into os.environ, which setdefault would keep.
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_TOKEN"] = token
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment("/Shared/fuelsignal-jump-model")

    project_config = load_project_config()
    modelling_config = project_config["modelling"]
    fuel_types = modelling_config["included_fuel_types"]
    n_folds = modelling_config["walk_forward_folds"]
    min_train_days = modelling_config["walk_forward_min_train_days"]
    test_days = modelling_config["walk_forward_test_days"]
    run_id = f"train-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    try:
        eligibility_check = client.execute(
            f"SELECT count(*) FROM {GOLD_SCHEMA}.gold_model_eligibility"
        )
        if int(eligibility_check["result"]["data_array"][0][0]) == 0:
            raise RuntimeError(
                "gold_model_eligibility is empty - run scripts/run_model_eligibility.py first"
            )

        print("Pulling training data from Databricks (features + labels)...", file=sys.stderr)
        df = fetch_training_data(client, fuel_types)
        df["market_date"] = df["market_date"].dt.date
        df[TARGET_COLUMN] = df[TARGET_COLUMN].astype(bool)
        df["jump_today"] = df["jump_today"].astype(bool)
        print(f"  {len(df)} rows, {df['station_id'].nunique()} stations", file=sys.stderr)

        market_series = fetch_market_series(client, fuel_types)

        start_date: date = df["market_date"].min()
        end_date: date = df["market_date"].max()
        folds = build_walk_forward_folds(start_date, end_date, min_train_days, test_days, n_folds)
        print(
            f"Built {len(folds)} walk-forward folds ({start_date} to {end_date})", file=sys.stderr
        )
        if not folds:
            raise RuntimeError(
                f"No walk-forward folds fit in the available date range "
                f"({start_date} to {end_date}, {(end_date - start_date).days} days) - need at least "
                f"{min_train_days + test_days} days"
            )

        with mlflow.start_run(run_name=run_id) as parent_run:
            mlflow.log_params(
                {
                    "fuel_types": ",".join(fuel_types),
                    "n_folds_configured": n_folds,
                    "n_folds_actual": len(folds),
                    "min_train_days": min_train_days,
                    "test_days": test_days,
                    "feature_count": len(FEATURE_COLUMNS),
                    "code_version": git_commit_short(),
                    "eligible_rows": len(df),
                }
            )
            mlflow.set_tags({"phase": "week2-phase2-modelling", "target": TARGET_COLUMN})

            fold_summaries = []
            final_fold_model: lgb.LGBMClassifier | None = None
            for fold in folds:
                print(
                    f"Fold {fold.fold_index}: train<= {fold.train_end}, test={fold.test_start}..{fold.test_end}",
                    file=sys.stderr,
                )
                train_df = df[
                    (df["market_date"] >= fold.train_start) & (df["market_date"] <= fold.train_end)
                ]
                test_df = df[
                    (df["market_date"] >= fold.test_start) & (df["market_date"] <= fold.test_end)
                ].copy()
                train_market = market_series[
                    (market_series["market_date"] >= fold.train_start)
                    & (market_series["market_date"] <= fold.train_end)
                ]

                if train_df.empty or test_df.empty:
                    print(
                        f"  fold {fold.fold_index}: skipped (empty train or test slice)",
                        file=sys.stderr,
                    )
                    continue

                lgb_model = train_lightgbm(train_df)
                final_fold_model = lgb_model
                x_test = test_df[FEATURE_COLUMNS + CATEGORICAL_COLUMNS].copy()
                x_test["fuel_type"] = x_test["fuel_type"].astype("category")
                test_df["lgb_proba"] = lgb_model.predict_proba(x_test)[:, 1]
                test_df["lgb_pred"] = test_df["lgb_proba"] >= 0.5

                baseline_cycle_lengths = {}
                for fuel_type in fuel_types:
                    preds, cycle_length = run_baseline(train_market, df, fuel_type)
                    baseline_cycle_lengths[fuel_type] = cycle_length
                    test_mask = test_df["fuel_type"] == fuel_type
                    test_df.loc[test_mask, "baseline_pred"] = preds.reindex(
                        test_df.index[test_mask]
                    ).to_numpy()
                test_df["baseline_pred"] = test_df["baseline_pred"].astype(bool)

                with mlflow.start_run(run_name=f"fold_{fold.fold_index}", nested=True):
                    fold_params = {
                        "fold_index": fold.fold_index,
                        "train_start": str(fold.train_start),
                        "train_end": str(fold.train_end),
                        "test_start": str(fold.test_start),
                        "test_end": str(fold.test_end),
                        "train_rows": len(train_df),
                        "test_rows": len(test_df),
                    }
                    for fuel_type, cycle_length in baseline_cycle_lengths.items():
                        fold_params[f"baseline_cycle_length_{fuel_type}"] = cycle_length
                    mlflow.log_params(fold_params)

                    # Accumulate every metric into one dict and send it as a single
                    # batch call (mlflow.log_metrics) - individual mlflow.log_metric
                    # calls (one REST round-trip each) made this pipeline take upwards
                    # of an hour per fold in this network environment; batching cuts
                    # ~150 round-trips per fold down to one.
                    batched_metrics: dict[str, float] = {}
                    fuel_metrics = {}
                    for fuel_type in [*fuel_types, "ALL"]:
                        fuel_test = (
                            test_df
                            if fuel_type == "ALL"
                            else test_df[test_df["fuel_type"] == fuel_type]
                        )
                        if fuel_test.empty:
                            continue
                        y_true = fuel_test[TARGET_COLUMN].to_numpy().astype(int)

                        lgb_metrics = row_grain_metrics(
                            y_true,
                            fuel_test["lgb_pred"].to_numpy().astype(int),
                            fuel_test["lgb_proba"].to_numpy(),
                        )
                        baseline_metrics = row_grain_metrics(
                            y_true,
                            fuel_test["baseline_pred"].to_numpy().astype(int),
                            fuel_test["baseline_pred"].to_numpy().astype(float),
                        )
                        for metric_name, value in lgb_metrics.items():
                            if value == value:  # skip NaN (PR-AUC when no positives)
                                batched_metrics[f"lightgbm_{fuel_type}_{metric_name}"] = value
                        for metric_name, value in baseline_metrics.items():
                            if value == value:
                                batched_metrics[f"baseline_{fuel_type}_{metric_name}"] = value

                        entry = {"lightgbm": lgb_metrics, "baseline": baseline_metrics}
                        if fuel_type != "ALL":
                            lgb_market = market_day_metrics(test_df, "lgb_pred", fuel_type)
                            baseline_market = market_day_metrics(
                                test_df, "baseline_pred", fuel_type
                            )
                            entry["lightgbm_market_day"] = lgb_market
                            entry["baseline_market_day"] = baseline_market
                            for metric_name, value in lgb_market.items():
                                if value is not None:
                                    batched_metrics[
                                        f"lightgbm_{fuel_type}_market_{metric_name}"
                                    ] = value
                            for metric_name, value in baseline_market.items():
                                if value is not None:
                                    batched_metrics[
                                        f"baseline_{fuel_type}_market_{metric_name}"
                                    ] = value
                        fuel_metrics[fuel_type] = entry

                    mlflow.log_metrics(batched_metrics)
                    fold_summaries.append(
                        {
                            "fold_index": fold.fold_index,
                            "train_start": str(fold.train_start),
                            "train_end": str(fold.train_end),
                            "test_start": str(fold.test_start),
                            "test_end": str(fold.test_end),
                            "metrics": fuel_metrics,
                        }
                    )

            mlflow.log_dict({"fold_summaries": fold_summaries}, "fold_summaries.json")
            if final_fold_model is not None:
                # Log the model artifact once (on the parent run), not once per fold -
                # this is the fold-4 (most recent walk-forward) model, the closest
                # proxy available today to what would actually be deployed next. Model
                # artifact uploads are the slow part of this pipeline in this network
                # environment; logging once instead of once-per-fold avoids repeated
                # multi-minute uploads that added no evaluation value (fold metrics
                # already capture each fold's performance without needing every fold's
                # model artifact preserved).
                print("Logging final model artifact to MLflow...", file=sys.stderr)
                mlflow.lightgbm.log_model(final_fold_model, name="model")
            summary = {
                "run_id": run_id,
                "mlflow_run_id": parent_run.info.run_id,
                "mlflow_experiment_id": parent_run.info.experiment_id,
                "fold_count": len(fold_summaries),
                "fold_summaries": fold_summaries,
            }
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Model training failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code != 0:
        # Databricks' git_source spark_python_task execution (an exec-style,
        # non-notebook context) treats *any* raised SystemExit - even SystemExit(0)
        # - as a task failure (live-verified 2026-07-18). Only raise on a genuine
        # non-zero exit code.
        raise SystemExit(_exit_code)
