"""Compute and persist the model-eligibility filter for the price-jump modelling phase.

Must run before scripts/train_jump_model.py. Computes, per station x fuel_type series
(restricted to the six fuel types approved for the first model iteration - see
config/project.yml -> modelling.included_fuel_types), the observation count and
extreme-price-change rate from the already-populated Gold layer, applies the
eligibility rule (src/fuelsignal/modelling/eligibility.py), and writes every series -
eligible and excluded alike - to gold_model_eligibility. Nothing is deleted from Gold;
this table is the audit trail for which series the model training step will use.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


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
from fuelsignal.modelling.eligibility import evaluate_eligibility  # noqa: E402

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"

ELIGIBILITY_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.gold_model_eligibility (
    station_id STRING NOT NULL,
    fuel_type STRING NOT NULL,
    total_observations INT,
    date_span_days INT,
    extreme_change_count INT,
    extreme_change_rate DOUBLE,
    min_observations_threshold INT,
    max_extreme_change_rate_threshold DOUBLE,
    is_eligible BOOLEAN,
    exclusion_reason STRING,
    evaluated_at TIMESTAMP,
    _pipeline_run_id STRING
)
USING DELTA
COMMENT 'Model-eligibility audit trail: every station x fuel_type series considered for
training, eligible and excluded alike - never deleted, only flagged.'
TBLPROPERTIES ('quality' = 'gold')
"""


def fetch_series_stats(
    client: DatabricksSqlClient, gold_schema: str, fuel_types: list[str]
) -> list[dict[str, Any]]:
    """Pull per-series observation counts and extreme-change rates in one query."""
    fuel_list = ", ".join(sql_literal(f) for f in fuel_types)
    result = client.execute(
        f"""
        WITH counts AS (
          SELECT station_id, fuel_type, count(*) total_observations,
            datediff(max(market_date), min(market_date)) + 1 date_span_days
          FROM {gold_schema}.gold_station_daily_market
          WHERE fuel_type IN ({fuel_list})
          GROUP BY station_id, fuel_type
        ),
        extremes AS (
          SELECT station_id, fuel_type,
            sum(CASE WHEN abs(rolling_14d_price_change_cpl) > 100 THEN 1 ELSE 0 END) extreme_change_count
          FROM {gold_schema}.gold_market_cycle_features
          WHERE fuel_type IN ({fuel_list})
          GROUP BY station_id, fuel_type
        )
        SELECT c.station_id, c.fuel_type, c.total_observations, c.date_span_days,
          coalesce(e.extreme_change_count, 0) extreme_change_count
        FROM counts c
        LEFT JOIN extremes e ON c.station_id = e.station_id AND c.fuel_type = e.fuel_type
        """
    )
    columns = [
        "station_id",
        "fuel_type",
        "total_observations",
        "date_span_days",
        "extreme_change_count",
    ]
    return [dict(zip(columns, row, strict=True)) for row in result["result"]["data_array"]]


def main() -> int:
    load_env()
    host, token = databricks_auth()
    client = DatabricksSqlClient(host=host, token=token)
    gold_schema = f"{CATALOG}.{SCHEMA_PREFIX}_gold"
    run_id = f"eligibility-{git_commit_short()}"

    project_config = load_project_config()
    modelling_config = project_config["modelling"]
    fuel_types = modelling_config["included_fuel_types"]
    min_observations = modelling_config["min_observations_per_series"]
    max_extreme_rate = modelling_config["max_extreme_change_rate"]

    silver_schema = f"{CATALOG}.{SCHEMA_PREFIX}_silver"
    try:
        for schema, table in (
            (silver_schema, "silver_fuel_prices"),
            (gold_schema, "gold_station_daily_market"),
            (gold_schema, "gold_market_cycle_features"),
        ):
            result = client.execute(f"SELECT count(*) FROM {schema}.{table}")
            if int(result["result"]["data_array"][0][0]) == 0:
                raise RuntimeError(
                    f"{schema}.{table} is empty - run the Bronze/Silver/Gold pipelines first"
                )

        print("Creating gold_model_eligibility (if missing)...", file=sys.stderr)
        client.execute(ELIGIBILITY_DDL.format(schema=gold_schema))
        client.execute(
            f"DELETE FROM {gold_schema}.gold_model_eligibility WHERE fuel_type IN ({', '.join(sql_literal(f) for f in fuel_types)})"
        )

        print(f"Computing series stats for {fuel_types}...", file=sys.stderr)
        series_stats = fetch_series_stats(client, gold_schema, fuel_types)
        print(f"  {len(series_stats)} station x fuel_type series found", file=sys.stderr)

        rows_sql = []
        eligible_count = 0
        exclusion_counts: dict[str, int] = {}
        for series in series_stats:
            total_obs = int(series["total_observations"])
            extreme_count = int(series["extreme_change_count"])
            extreme_rate = extreme_count / total_obs if total_obs else 1.0
            decision = evaluate_eligibility(
                total_obs, extreme_rate, min_observations, max_extreme_rate
            )
            if decision.is_eligible:
                eligible_count += 1
            else:
                exclusion_counts[decision.exclusion_reason] = (
                    exclusion_counts.get(decision.exclusion_reason, 0) + 1
                )
            rows_sql.append(
                "("
                + ", ".join(
                    [
                        sql_literal(series["station_id"]),
                        sql_literal(series["fuel_type"]),
                        str(total_obs),
                        str(int(series["date_span_days"])),
                        str(extreme_count),
                        repr(round(extreme_rate, 6)),
                        str(min_observations),
                        repr(max_extreme_rate),
                        "true" if decision.is_eligible else "false",
                        sql_literal(decision.exclusion_reason),
                        "current_timestamp()",
                        sql_literal(run_id),
                    ]
                )
                + ")"
            )

        print("Writing eligibility rows...", file=sys.stderr)
        batch_size = 500
        for i in range(0, len(rows_sql), batch_size):
            batch = rows_sql[i : i + batch_size]
            client.execute(
                f"""
                INSERT INTO {gold_schema}.gold_model_eligibility
                (station_id, fuel_type, total_observations, date_span_days, extreme_change_count,
                 extreme_change_rate, min_observations_threshold, max_extreme_change_rate_threshold,
                 is_eligible, exclusion_reason, evaluated_at, _pipeline_run_id)
                VALUES {", ".join(batch)}
                """
            )

        eligible_row_count_result = client.execute(
            f"""
            SELECT count(*) FROM {gold_schema}.gold_daily_pricing_inputs f
            JOIN {gold_schema}.gold_model_eligibility e
              ON f.station_id = e.station_id AND f.fuel_type = e.fuel_type
            WHERE e.is_eligible AND e.fuel_type IN ({', '.join(sql_literal(x) for x in fuel_types)})
            """
        )
        eligible_row_count = int(eligible_row_count_result["result"]["data_array"][0][0])

        summary = {
            "run_id": run_id,
            "fuel_types": fuel_types,
            "min_observations_threshold": min_observations,
            "max_extreme_change_rate_threshold": max_extreme_rate,
            "total_series": len(series_stats),
            "eligible_series": eligible_count,
            "excluded_series": len(series_stats) - eligible_count,
            "exclusion_reason_counts": exclusion_counts,
            "eligible_gold_daily_pricing_inputs_rows": eligible_row_count,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Model eligibility computation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code != 0:
        # Databricks' git_source spark_python_task execution (an exec-style,
        # non-notebook context) treats *any* raised SystemExit - even SystemExit(0)
        # - as a task failure (live-verified 2026-07-18). Only raise on a genuine
        # non-zero exit code.
        raise SystemExit(_exit_code)
