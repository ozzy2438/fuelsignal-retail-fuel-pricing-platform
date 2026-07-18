"""Per-fuel-type LightGBM decision threshold calibration (Week 2 Phase 3, Part 1).

For each walk-forward fold, splits the TRAIN period into a fit-train slice and a
validation slice (the last `validation_days` of train). A model trained on fit-train
only is used to sweep a threshold grid on the validation slice (never on the fold's
own held-out test period). Validation predictions are pooled across all folds per
fuel type, the business-oriented selection rule
(src/fuelsignal/modelling/threshold_selection.py) picks one threshold per fuel type,
and that threshold is then applied - for the first time - to each fold's untouched
test-period predictions (from a model trained on the FULL fold.train, matching
scripts/train_jump_model.py) to report final test performance.

Chosen thresholds are written to config/model_thresholds.yml and logged to MLflow.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import average_precision_score

try:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
except NameError:
    # Databricks git_source spark_python_task executes via an exec-style context
    # where __file__ is undefined - the working directory is the repo checkout root.
    PROJECT_ROOT = Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from deploy_databricks_foundation import DatabricksSqlClient, DeploymentError  # noqa: E402
from run_ingestion_pipeline import databricks_auth, git_commit_short  # noqa: E402
from train_jump_model import (  # noqa: E402
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    fetch_market_series,
    fetch_training_data,
    run_baseline,
    train_lightgbm,
)

from fuelsignal.config import load_env, load_project_config  # noqa: E402
from fuelsignal.modelling.evaluation import (  # noqa: E402
    aggregate_to_market_day,
    evaluate_market_day_warnings,
)
from fuelsignal.modelling.threshold_selection import (  # noqa: E402
    ThresholdCandidate,
    select_threshold,
)
from fuelsignal.modelling.walk_forward import build_walk_forward_folds  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "model_thresholds.yml"


def market_day_false_alarms_per_month(
    frame: pd.DataFrame, predicted_col: str, fuel_type: str
) -> tuple[dict[str, Any], float]:
    """False-alarm/lead-time stats plus the false-alarms-per-30-day-month rate."""
    fuel_df = frame[frame["fuel_type"] == fuel_type]
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
    days_covered = len(daily)
    months_covered = days_covered / 30.0 if days_covered else 1.0
    false_alarms_per_month = result.false_alarm_count / months_covered
    return (
        {
            "warning_count": result.warning_count,
            "false_alarm_count": result.false_alarm_count,
            "matched_jump_count": result.matched_jump_count,
            "average_lead_time_days": result.average_lead_time_days,
            "market_days_evaluated": days_covered,
        },
        false_alarms_per_month,
    )


def _row_precision_recall_f1(pred: np.ndarray, y_true: np.ndarray) -> dict[str, float]:
    tp = int(((pred == 1) & (y_true == 1)).sum())
    fp = int(((pred == 1) & (y_true == 0)).sum())
    fn = int(((pred == 0) & (y_true == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def sweep_grid(frame: pd.DataFrame, fuel_type: str, grid: list[float]) -> list[ThresholdCandidate]:
    """Row-grain + market-day metrics for every candidate threshold, one fuel type."""
    fuel_frame = frame[frame["fuel_type"] == fuel_type]
    y_true = fuel_frame[TARGET_COLUMN].to_numpy().astype(int)
    candidates = []
    for threshold in grid:
        y_pred = (fuel_frame["lgb_proba"].to_numpy() >= threshold).astype(int)
        tp = int(((y_pred == 1) & (y_true == 1)).sum())
        fp = int(((y_pred == 1) & (y_true == 0)).sum())
        fn = int(((y_pred == 0) & (y_true == 1)).sum())
        tn = int(((y_pred == 0) & (y_true == 0)).sum())
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0

        temp = fuel_frame.copy()
        temp["_pred_at_threshold"] = fuel_frame["lgb_proba"] >= threshold
        market_stats, fa_per_month = market_day_false_alarms_per_month(
            temp, "_pred_at_threshold", fuel_type
        )
        candidates.append(
            ThresholdCandidate(
                threshold=threshold,
                precision=precision,
                recall=recall,
                f1=f1,
                false_positive_rate=fpr,
                alerts=market_stats["warning_count"],
                false_alarms_per_market_month=fa_per_month,
                average_lead_time_days=market_stats["average_lead_time_days"],
            )
        )
    return candidates


def main() -> int:
    load_env()
    host, token = databricks_auth()
    client = DatabricksSqlClient(host=host, token=token)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_TOKEN"] = token
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment("/Shared/fuelsignal-jump-model")

    project_config = load_project_config()
    modelling_config = project_config["modelling"]
    calib_config = project_config["threshold_calibration"]
    fuel_types = modelling_config["included_fuel_types"]
    n_folds = modelling_config["walk_forward_folds"]
    min_train_days = modelling_config["walk_forward_min_train_days"]
    test_days = modelling_config["walk_forward_test_days"]
    validation_days = calib_config["validation_days"]
    grid = list(
        np.round(
            np.arange(
                calib_config["threshold_grid_min"],
                calib_config["threshold_grid_max"] + 1e-9,
                calib_config["threshold_grid_step"],
            ),
            2,
        ).tolist()
    )
    run_id = f"calibrate-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    try:
        print("Pulling training data...", file=sys.stderr)
        df = fetch_training_data(client, fuel_types)
        df["market_date"] = df["market_date"].dt.date
        df[TARGET_COLUMN] = df[TARGET_COLUMN].astype(bool)
        df["jump_today"] = df["jump_today"].astype(bool)
        print(f"  {len(df)} rows", file=sys.stderr)
        market_series = fetch_market_series(client, fuel_types)

        start_date: date = df["market_date"].min()
        end_date: date = df["market_date"].max()
        folds = build_walk_forward_folds(start_date, end_date, min_train_days, test_days, n_folds)
        if not folds:
            raise RuntimeError("No walk-forward folds fit in the available date range")
        print(f"Built {len(folds)} folds", file=sys.stderr)

        pooled_validation = []
        pooled_test = []
        for fold in folds:
            fit_train_end = fold.train_end - timedelta(days=validation_days)
            validation_start = fit_train_end + timedelta(days=1)

            fit_train_df = df[
                (df["market_date"] >= fold.train_start) & (df["market_date"] <= fit_train_end)
            ]
            validation_df = df[
                (df["market_date"] >= validation_start) & (df["market_date"] <= fold.train_end)
            ].copy()
            full_train_df = df[
                (df["market_date"] >= fold.train_start) & (df["market_date"] <= fold.train_end)
            ]
            test_df = df[
                (df["market_date"] >= fold.test_start) & (df["market_date"] <= fold.test_end)
            ].copy()

            if fit_train_df.empty or validation_df.empty or test_df.empty:
                print(f"  fold {fold.fold_index}: skipped (empty slice)", file=sys.stderr)
                continue

            print(
                f"Fold {fold.fold_index}: fit_train<= {fit_train_end}, validation={validation_start}..{fold.train_end}, test={fold.test_start}..{fold.test_end}",
                file=sys.stderr,
            )

            calibration_model = train_lightgbm(fit_train_df)
            x_val = validation_df[FEATURE_COLUMNS + CATEGORICAL_COLUMNS].copy()
            x_val["fuel_type"] = x_val["fuel_type"].astype("category")
            validation_df["lgb_proba"] = calibration_model.predict_proba(x_val)[:, 1]
            validation_df["fold_index"] = fold.fold_index
            pooled_validation.append(validation_df)

            test_model = train_lightgbm(full_train_df)
            x_test = test_df[FEATURE_COLUMNS + CATEGORICAL_COLUMNS].copy()
            x_test["fuel_type"] = x_test["fuel_type"].astype("category")
            test_df["lgb_proba"] = test_model.predict_proba(x_test)[:, 1]
            test_df["fold_index"] = fold.fold_index

            train_market = market_series[
                (market_series["market_date"] >= fold.train_start)
                & (market_series["market_date"] <= fold.train_end)
            ]
            test_df["baseline_pred"] = False
            for fuel_type in fuel_types:
                preds, _cycle_length = run_baseline(train_market, df, fuel_type)
                fuel_mask = test_df["fuel_type"] == fuel_type
                test_df.loc[fuel_mask, "baseline_pred"] = preds.reindex(
                    test_df.index[fuel_mask]
                ).to_numpy()
            test_df["baseline_pred"] = test_df["baseline_pred"].astype(bool)

            pooled_test.append(test_df)

        validation_all = pd.concat(pooled_validation, ignore_index=True)
        test_all = pd.concat(pooled_test, ignore_index=True)
        print(
            f"Pooled validation rows: {len(validation_all)}, test rows: {len(test_all)}",
            file=sys.stderr,
        )

        with mlflow.start_run(run_name=run_id) as parent_run:
            mlflow.log_params(
                {
                    "fuel_types": ",".join(fuel_types),
                    "validation_days": validation_days,
                    "grid_size": len(grid),
                    "min_recall": calib_config["min_recall"],
                    "max_false_alarms_per_market_month": calib_config[
                        "max_false_alarms_per_market_month"
                    ],
                    "min_avg_lead_time_days": calib_config["min_avg_lead_time_days"],
                    "code_version": git_commit_short(),
                }
            )
            mlflow.set_tags({"phase": "week2-phase3-threshold-calibration"})

            fuel_results: dict[str, Any] = {}
            batched_metrics: dict[str, float] = {}
            for fuel_type in fuel_types:
                candidates = sweep_grid(validation_all, fuel_type, grid)
                selection = select_threshold(
                    candidates,
                    min_recall=calib_config["min_recall"],
                    max_false_alarms_per_market_month=calib_config[
                        "max_false_alarms_per_market_month"
                    ],
                    min_avg_lead_time_days=calib_config["min_avg_lead_time_days"],
                )

                fuel_val = validation_all[validation_all["fuel_type"] == fuel_type]
                y_true_val = fuel_val[TARGET_COLUMN].to_numpy().astype(int)
                pr_auc = (
                    float(average_precision_score(y_true_val, fuel_val["lgb_proba"]))
                    if y_true_val.sum() > 0
                    else float("nan")
                )

                chosen = (
                    selection.chosen_threshold if selection.chosen_threshold is not None else 0.5
                )
                fallback_used = selection.chosen_threshold is None

                # Final untouched test-period performance at three approaches: the
                # calibrated threshold, the previous shared default, and the
                # rule-based baseline (baseline_pred is threshold-free by
                # construction).
                fuel_test = test_all[test_all["fuel_type"] == fuel_type]
                y_true_test = fuel_test[TARGET_COLUMN].to_numpy().astype(int)

                pred_calibrated = (fuel_test["lgb_proba"].to_numpy() >= chosen).astype(int)
                pred_default = (fuel_test["lgb_proba"].to_numpy() >= 0.5).astype(int)
                pred_baseline = fuel_test["baseline_pred"].to_numpy().astype(int)

                test_metrics = {
                    "calibrated": _row_precision_recall_f1(pred_calibrated, y_true_test),
                    "shared_default_0.5": _row_precision_recall_f1(pred_default, y_true_test),
                    "rule_based_baseline": _row_precision_recall_f1(pred_baseline, y_true_test),
                }

                fuel_results[fuel_type] = {
                    "pr_auc_validation": pr_auc,
                    "chosen_threshold": chosen,
                    "fallback_to_0.5": fallback_used,
                    "selection_reason": selection.reason,
                    "validation_candidate": (
                        {
                            "precision": selection.candidate.precision,
                            "recall": selection.candidate.recall,
                            "f1": selection.candidate.f1,
                            "false_positive_rate": selection.candidate.false_positive_rate,
                            "alerts": selection.candidate.alerts,
                            "false_alarms_per_market_month": selection.candidate.false_alarms_per_market_month,
                            "average_lead_time_days": selection.candidate.average_lead_time_days,
                        }
                        if selection.candidate
                        else None
                    ),
                    "test_metrics": test_metrics,
                    "grid": [
                        {
                            "threshold": c.threshold,
                            "precision": c.precision,
                            "recall": c.recall,
                            "f1": c.f1,
                            "false_positive_rate": c.false_positive_rate,
                            "alerts": c.alerts,
                            "false_alarms_per_market_month": c.false_alarms_per_market_month,
                            "average_lead_time_days": c.average_lead_time_days,
                        }
                        for c in candidates
                    ],
                }

                batched_metrics[f"{fuel_type}_chosen_threshold"] = chosen
                batched_metrics[f"{fuel_type}_pr_auc_validation"] = (
                    pr_auc if pr_auc == pr_auc else -1.0
                )
                batched_metrics[f"{fuel_type}_test_calibrated_precision"] = test_metrics[
                    "calibrated"
                ]["precision"]
                batched_metrics[f"{fuel_type}_test_calibrated_recall"] = test_metrics["calibrated"][
                    "recall"
                ]
                batched_metrics[f"{fuel_type}_test_calibrated_f1"] = test_metrics["calibrated"][
                    "f1"
                ]
                batched_metrics[f"{fuel_type}_test_default_precision"] = test_metrics[
                    "shared_default_0.5"
                ]["precision"]
                batched_metrics[f"{fuel_type}_test_default_recall"] = test_metrics[
                    "shared_default_0.5"
                ]["recall"]
                batched_metrics[f"{fuel_type}_test_default_f1"] = test_metrics[
                    "shared_default_0.5"
                ]["f1"]
                batched_metrics[f"{fuel_type}_test_baseline_precision"] = test_metrics[
                    "rule_based_baseline"
                ]["precision"]
                batched_metrics[f"{fuel_type}_test_baseline_recall"] = test_metrics[
                    "rule_based_baseline"
                ]["recall"]
                batched_metrics[f"{fuel_type}_test_baseline_f1"] = test_metrics[
                    "rule_based_baseline"
                ]["f1"]

            mlflow.log_metrics(batched_metrics)
            mlflow.log_dict(fuel_results, "threshold_calibration_results.json")

            config_out = {
                "calibrated_at": datetime.now(timezone.utc).isoformat(),
                "code_version": git_commit_short(),
                "mlflow_run_id": parent_run.info.run_id,
                "selection_rule": {
                    "min_recall": calib_config["min_recall"],
                    "max_false_alarms_per_market_month": calib_config[
                        "max_false_alarms_per_market_month"
                    ],
                    "min_avg_lead_time_days": calib_config["min_avg_lead_time_days"],
                    "description": (
                        "Among validation-set thresholds satisfying recall >= min_recall AND "
                        "false_alarms_per_market_month <= max_false_alarms_per_market_month AND "
                        "average_lead_time_days >= min_avg_lead_time_days, pick the one with the "
                        "highest F1. Never selects by F1 alone."
                    ),
                },
                "thresholds": {
                    fuel: {
                        "threshold": result["chosen_threshold"],
                        "fallback_to_0.5": result["fallback_to_0.5"],
                        "selection_reason": result["selection_reason"],
                        "validation_precision": (
                            result["validation_candidate"]["precision"]
                            if result["validation_candidate"]
                            else None
                        ),
                        "validation_recall": (
                            result["validation_candidate"]["recall"]
                            if result["validation_candidate"]
                            else None
                        ),
                        "validation_f1": (
                            result["validation_candidate"]["f1"]
                            if result["validation_candidate"]
                            else None
                        ),
                        "validation_false_alarms_per_market_month": (
                            result["validation_candidate"]["false_alarms_per_market_month"]
                            if result["validation_candidate"]
                            else None
                        ),
                        "validation_average_lead_time_days": (
                            result["validation_candidate"]["average_lead_time_days"]
                            if result["validation_candidate"]
                            else None
                        ),
                        "pr_auc_validation": result["pr_auc_validation"],
                    }
                    for fuel, result in fuel_results.items()
                },
            }
            with open(CONFIG_PATH, "w") as f:
                yaml.safe_dump(config_out, f, sort_keys=False, default_flow_style=False)
            mlflow.log_artifact(str(CONFIG_PATH))

        summary = {
            "run_id": run_id,
            "mlflow_run_id": parent_run.info.run_id,
            "fuel_results": fuel_results,
        }
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Threshold calibration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
