"""Market-level seven-day price forecast (Week 2 Phase 3, Part 2).

Builds a market-level (fuel_type x market_date) daily median-price series from
gold_price_jump_labels plus date-level context (day_of_week, is_public_holiday,
tgp_7d_change_cpl) aggregated from gold_market_cycle_features, engineers
trailing-only rolling features, and forecasts the market median price at
horizons of 1, 3 and 7 days ahead for U91/E10/P95/P98/DL/PDL. LPG/E85/B20 are
excluded (config/project.yml -> price_forecast.included_fuel_types), same
rationale as the jump model.

Compares four methods per horizon per fuel type, all evaluated only on
walk-forward test periods a model never trained on: persistence (last
observed price), a 7-day moving average, a linear trend extrapolated forward,
and a LightGBM regressor (fuel_type as a native categorical feature, one
shared model per horizon per fold). Reports MAE, RMSE, MAPE, WAPE and
directional accuracy overall and broken out by market phase (jump/decline/
other, from src/fuelsignal/modelling/forecast_metrics.py).

Does not touch pricing policy or make any commercial-impact claim, and does
not create HOLD/FOLLOW/LEAD decisions - see docs/price-forecast.md for the
explicit scope boundary.
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
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from deploy_databricks_foundation import DatabricksSqlClient, DeploymentError  # noqa: E402
from run_ingestion_pipeline import databricks_auth, git_commit_short, sql_literal  # noqa: E402

from fuelsignal.config import load_env, load_project_config  # noqa: E402
from fuelsignal.modelling.baseline import days_since_last_jump_series  # noqa: E402
from fuelsignal.modelling.forecast_baselines import (  # noqa: E402
    linear_trend_forecast,
    moving_average_forecast,
    persistence_forecast,
)
from fuelsignal.modelling.forecast_metrics import (  # noqa: E402
    classify_market_phase,
    directional_accuracy,
    mape,
    wape,
)
from fuelsignal.modelling.walk_forward import build_walk_forward_folds  # noqa: E402

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"
GOLD_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_gold"

FEATURE_COLUMNS = [
    "market_median_price_cpl",
    "market_daily_change_cpl",
    "rolling_7d_mean_price",
    "rolling_7d_std_price",
    "rolling_7d_min_price",
    "rolling_7d_max_price",
    "rolling_14d_mean_price",
    "rolling_14d_std_price",
    "rolling_14d_min_price",
    "rolling_14d_max_price",
    "days_since_last_jump",
    "tgp_7d_change_cpl",
    "day_of_week",
    "is_public_holiday",
]
CATEGORICAL_COLUMNS = ["fuel_type"]
RESULTS_PATH = PROJECT_ROOT / "config" / "price_forecast_results.json"


def fetch_market_price_series(client: DatabricksSqlClient, fuel_types: list[str]) -> pd.DataFrame:
    """Pull the market-level (fuel_type x date) price series plus date-level context."""
    fuel_list = ", ".join(sql_literal(f) for f in fuel_types)
    sql = f"""
        WITH label_series AS (
            SELECT fuel_type, market_date, market_median_price_cpl,
                   market_daily_change_cpl, jump_today
            FROM {GOLD_SCHEMA}.gold_price_jump_labels
            WHERE fuel_type IN ({fuel_list})
        ),
        context_features AS (
            SELECT fuel_type, market_date,
                   MAX(day_of_week) AS day_of_week,
                   MAX(CASE WHEN is_public_holiday THEN 1 ELSE 0 END) AS is_public_holiday,
                   AVG(tgp_7d_change_cpl) AS tgp_7d_change_cpl
            FROM {GOLD_SCHEMA}.gold_market_cycle_features
            WHERE fuel_type IN ({fuel_list})
            GROUP BY fuel_type, market_date
        )
        SELECT l.fuel_type, l.market_date, l.market_median_price_cpl,
               l.market_daily_change_cpl, l.jump_today,
               c.day_of_week, c.is_public_holiday, c.tgp_7d_change_cpl
        FROM label_series l
        LEFT JOIN context_features c
          ON l.fuel_type = c.fuel_type AND l.market_date = c.market_date
        ORDER BY l.fuel_type, l.market_date
    """
    return client.execute_to_dataframe(sql)


def build_feature_frame(
    raw: pd.DataFrame, rolling_windows: list[int], horizons: list[int]
) -> pd.DataFrame:
    """Trailing-only rolling features, days-since-last-jump, and shifted horizon
    targets (price and phase inputs), engineered independently per fuel type."""
    frame = raw.copy()
    frame["market_date"] = pd.to_datetime(frame["market_date"]).dt.date
    frame["jump_today"] = frame["jump_today"].astype(bool)
    frame["is_public_holiday"] = frame["is_public_holiday"].fillna(0).astype(int)

    pieces = []
    for _fuel_type, group in frame.groupby("fuel_type", sort=False):
        group = group.sort_values("market_date").reset_index(drop=True)
        prices = group["market_median_price_cpl"]
        for window in rolling_windows:
            roll = prices.rolling(window, min_periods=1)
            group[f"rolling_{window}d_mean_price"] = roll.mean()
            group[f"rolling_{window}d_std_price"] = roll.std().fillna(0.0)
            group[f"rolling_{window}d_min_price"] = roll.min()
            group[f"rolling_{window}d_max_price"] = roll.max()
        group["days_since_last_jump"] = pd.array(
            days_since_last_jump_series(list(group["jump_today"])), dtype="Float64"
        ).to_numpy(dtype=float, na_value=np.nan)
        for horizon in horizons:
            group[f"target_price_h{horizon}"] = prices.shift(-horizon)
            group[f"target_jump_today_h{horizon}"] = group["jump_today"].shift(-horizon)
            group[f"target_daily_change_h{horizon}"] = group["market_daily_change_cpl"].shift(
                -horizon
            )
        pieces.append(group)
    return pd.concat(pieces, ignore_index=True)


def add_baseline_predictions(
    frame: pd.DataFrame, horizons: list[int], ma_window: int, trend_window: int
) -> pd.DataFrame:
    """Row-wise persistence/moving-average/linear-trend predictions using only
    price history up to and including that row (never future prices)."""
    pieces = []
    for _fuel_type, group in frame.groupby("fuel_type", sort=False):
        group = group.sort_values("market_date").reset_index(drop=True)
        prices = group["market_median_price_cpl"].tolist()
        for horizon in horizons:
            persistence_col = []
            ma_col = []
            trend_col = []
            for i in range(len(prices)):
                history = prices[: i + 1]
                persistence_col.append(persistence_forecast(history))
                ma_col.append(moving_average_forecast(history, ma_window))
                trend_col.append(linear_trend_forecast(history, trend_window, horizon))
            group[f"baseline_persistence_h{horizon}"] = persistence_col
            group[f"baseline_ma_h{horizon}"] = ma_col
            group[f"baseline_trend_h{horizon}"] = trend_col
        pieces.append(group)
    return pd.concat(pieces, ignore_index=True)


def train_lightgbm_regressor(x_train: pd.DataFrame, y_train: pd.Series) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(
        objective="regression",
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=10,
        random_state=42,
        verbosity=-1,
    )
    model.fit(x_train, y_train, categorical_feature=CATEGORICAL_COLUMNS)
    return model


def _regression_metrics(
    actual: np.ndarray, predicted: np.ndarray, last_observed: np.ndarray
) -> dict[str, Any]:
    diff = actual - predicted
    return {
        "mae": float(np.mean(np.abs(diff))),
        "rmse": float(np.sqrt(np.mean(diff**2))),
        "mape": mape(actual.tolist(), predicted.tolist()),
        "wape": wape(actual.tolist(), predicted.tolist()),
        "directional_accuracy": directional_accuracy(
            last_observed.tolist(), actual.tolist(), predicted.tolist()
        ),
        "n": int(len(actual)),
    }


def evaluate_pooled_test(
    pooled_test: pd.DataFrame, horizon: int, fuel_types: list[str]
) -> dict[str, Any]:
    """Overall + phase-broken-out metrics for every method, one fuel type at a time."""
    target_col = f"target_price_h{horizon}"
    jump_col = f"target_jump_today_h{horizon}"
    change_col = f"target_daily_change_h{horizon}"
    methods = {
        "persistence": f"baseline_persistence_h{horizon}",
        "moving_average_7d": f"baseline_ma_h{horizon}",
        "linear_trend_14d": f"baseline_trend_h{horizon}",
        "lightgbm": f"pred_lgb_h{horizon}",
    }
    results: dict[str, Any] = {}
    for fuel_type in fuel_types:
        fuel_df = pooled_test[pooled_test["fuel_type"] == fuel_type].dropna(subset=[target_col])
        if fuel_df.empty:
            continue
        phases = fuel_df.apply(
            lambda row: classify_market_phase(bool(row[jump_col]), row[change_col]), axis=1
        )
        fuel_result: dict[str, Any] = {}
        for method_name, pred_col in methods.items():
            method_df = fuel_df.dropna(subset=[pred_col])
            if method_df.empty:
                continue
            method_phases = phases.loc[method_df.index]
            actual = method_df[target_col].to_numpy()
            predicted = method_df[pred_col].to_numpy()
            last_observed = method_df["market_median_price_cpl"].to_numpy()
            method_result = {"overall": _regression_metrics(actual, predicted, last_observed)}
            for phase in ("jump", "decline", "other"):
                phase_mask = (method_phases == phase).to_numpy()
                if phase_mask.sum() == 0:
                    continue
                method_result[phase] = _regression_metrics(
                    actual[phase_mask], predicted[phase_mask], last_observed[phase_mask]
                )
            fuel_result[method_name] = method_result
        results[fuel_type] = fuel_result
    return results


def main() -> int:
    load_env()
    host, token = databricks_auth()
    client = DatabricksSqlClient(host=host, token=token)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_TOKEN"] = token
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment("/Shared/fuelsignal-price-forecast")

    project_config = load_project_config()
    modelling_config = project_config["modelling"]
    forecast_config = project_config["price_forecast"]
    fuel_types = forecast_config["included_fuel_types"]
    horizons = forecast_config["horizons_days"]
    rolling_windows = forecast_config["rolling_windows_days"]
    ma_window = forecast_config["moving_average_window_days"]
    trend_window = forecast_config["linear_trend_window_days"]
    n_folds = modelling_config["walk_forward_folds"]
    min_train_days = modelling_config["walk_forward_min_train_days"]
    test_days = modelling_config["walk_forward_test_days"]
    run_id = f"forecast-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    try:
        print("Pulling market-level price series...", file=sys.stderr)
        raw = fetch_market_price_series(client, fuel_types)
        print(f"  {len(raw)} rows", file=sys.stderr)

        feature_frame = build_feature_frame(raw, rolling_windows, horizons)
        feature_frame = add_baseline_predictions(feature_frame, horizons, ma_window, trend_window)

        start_date: date = feature_frame["market_date"].min()
        end_date: date = feature_frame["market_date"].max()
        folds = build_walk_forward_folds(start_date, end_date, min_train_days, test_days, n_folds)
        if not folds:
            raise RuntimeError("No walk-forward folds fit in the available date range")
        print(f"Built {len(folds)} folds", file=sys.stderr)

        pooled_test_by_horizon: dict[int, list[pd.DataFrame]] = {h: [] for h in horizons}
        for fold in folds:
            train_mask = (feature_frame["market_date"] >= fold.train_start) & (
                feature_frame["market_date"] <= fold.train_end
            )
            test_mask = (feature_frame["market_date"] >= fold.test_start) & (
                feature_frame["market_date"] <= fold.test_end
            )
            train_df = feature_frame[train_mask]
            test_df = feature_frame[test_mask]
            if train_df.empty or test_df.empty:
                print(f"  fold {fold.fold_index}: skipped (empty slice)", file=sys.stderr)
                continue

            for horizon in horizons:
                target_col = f"target_price_h{horizon}"
                # Only the target needs to be non-null - tgp_7d_change_cpl is
                # permanently NaN for non-U91/DL fuel types and days_since_last_jump
                # is NaN before that fuel type's first observed jump; LightGBM
                # handles missing feature values natively rather than dropping rows.
                train_h = train_df.dropna(subset=[target_col])
                test_h = test_df.dropna(subset=[target_col]).copy()
                if train_h.empty or test_h.empty:
                    continue

                x_train = train_h[FEATURE_COLUMNS + CATEGORICAL_COLUMNS].copy()
                x_train["fuel_type"] = x_train["fuel_type"].astype("category")
                y_train = train_h[target_col]
                model = train_lightgbm_regressor(x_train, y_train)

                x_test = test_h[FEATURE_COLUMNS + CATEGORICAL_COLUMNS].copy()
                x_test["fuel_type"] = x_test["fuel_type"].astype("category")
                test_h[f"pred_lgb_h{horizon}"] = model.predict(x_test)
                test_h["fold_index"] = fold.fold_index
                pooled_test_by_horizon[horizon].append(test_h)

            print(
                f"Fold {fold.fold_index}: train={fold.train_start}..{fold.train_end}, test={fold.test_start}..{fold.test_end}",
                file=sys.stderr,
            )

        with mlflow.start_run(run_name=run_id) as parent_run:
            mlflow.log_params(
                {
                    "fuel_types": ",".join(fuel_types),
                    "horizons_days": ",".join(str(h) for h in horizons),
                    "rolling_windows_days": ",".join(str(w) for w in rolling_windows),
                    "moving_average_window_days": ma_window,
                    "linear_trend_window_days": trend_window,
                    "walk_forward_folds": n_folds,
                    "walk_forward_min_train_days": min_train_days,
                    "walk_forward_test_days": test_days,
                    "code_version": git_commit_short(),
                }
            )
            mlflow.set_tags({"phase": "week2-phase3-price-forecast"})

            full_results: dict[str, Any] = {}
            batched_metrics: dict[str, float] = {}
            best_method_by_horizon: dict[int, str] = {}
            for horizon in horizons:
                if not pooled_test_by_horizon[horizon]:
                    continue
                pooled_test = pd.concat(pooled_test_by_horizon[horizon], ignore_index=True)
                horizon_results = evaluate_pooled_test(pooled_test, horizon, fuel_types)
                full_results[f"h{horizon}"] = horizon_results

                method_wapes: dict[str, list[float]] = {}
                for fuel_type, fuel_result in horizon_results.items():
                    for method_name, method_result in fuel_result.items():
                        overall = method_result["overall"]
                        for metric_name in ("mae", "rmse", "directional_accuracy"):
                            value = overall.get(metric_name)
                            if value is not None:
                                batched_metrics[
                                    f"{fuel_type}_h{horizon}_{method_name}_{metric_name}"
                                ] = value
                        if overall.get("wape") is not None:
                            batched_metrics[f"{fuel_type}_h{horizon}_{method_name}_wape"] = overall[
                                "wape"
                            ]
                            method_wapes.setdefault(method_name, []).append(overall["wape"])

                if method_wapes:
                    avg_wape = {m: sum(v) / len(v) for m, v in method_wapes.items()}
                    best_method_by_horizon[horizon] = min(avg_wape, key=avg_wape.get)

            mlflow.log_metrics(batched_metrics)
            mlflow.set_tags(
                {f"best_method_h{h}": method for h, method in best_method_by_horizon.items()}
            )
            mlflow.log_dict(full_results, "price_forecast_results.json")

            summary_out = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "code_version": git_commit_short(),
                "mlflow_run_id": parent_run.info.run_id,
                "best_method_by_horizon": {f"h{h}": m for h, m in best_method_by_horizon.items()},
                "results": full_results,
            }
            with open(RESULTS_PATH, "w") as f:
                json.dump(summary_out, f, indent=2, sort_keys=True, default=str)
            mlflow.log_artifact(str(RESULTS_PATH))

        print(json.dumps(summary_out, indent=2, sort_keys=True, default=str))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Price forecast failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
