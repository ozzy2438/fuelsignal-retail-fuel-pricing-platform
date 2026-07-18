"""Report live FuelSignal Bronze/Silver ingestion validation metrics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    SCRIPTS_DIR = Path(__file__).resolve().parent
except NameError:
    # Databricks git_source spark_python_task executes via an exec-style context
    # where __file__ is undefined - the working directory is the repo checkout root.
    SCRIPTS_DIR = Path.cwd() / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from deploy_databricks_foundation import DatabricksSqlClient  # noqa: E402
from run_ingestion_pipeline import databricks_auth  # noqa: E402

QUERIES = {
    "row_counts": """
                SELECT 'fuelsignal.fuelsignal_bronze.bronze_fuelcheck_prices_raw' table_name,
                    count(*) row_count FROM fuelsignal.fuelsignal_bronze.bronze_fuelcheck_prices_raw
                UNION ALL SELECT 'fuelsignal.fuelsignal_bronze.bronze_fuelcheck_stations_raw',
                    count(*) FROM fuelsignal.fuelsignal_bronze.bronze_fuelcheck_stations_raw
                UNION ALL SELECT 'fuelsignal.fuelsignal_bronze.bronze_aip_tgp_raw',
                    count(*) FROM fuelsignal.fuelsignal_bronze.bronze_aip_tgp_raw
                UNION ALL SELECT 'fuelsignal.fuelsignal_bronze.bronze_public_holidays_raw',
                    count(*) FROM fuelsignal.fuelsignal_bronze.bronze_public_holidays_raw
                UNION ALL SELECT 'fuelsignal.fuelsignal_bronze.bronze_ingestion_audit',
                    count(*) FROM fuelsignal.fuelsignal_bronze.bronze_ingestion_audit
                UNION ALL SELECT 'fuelsignal.fuelsignal_silver.silver_fuel_prices',
                    count(*) FROM fuelsignal.fuelsignal_silver.silver_fuel_prices
                UNION ALL SELECT 'fuelsignal.fuelsignal_silver.silver_station_master',
                    count(*) FROM fuelsignal.fuelsignal_silver.silver_station_master
                UNION ALL SELECT 'fuelsignal.fuelsignal_silver.silver_terminal_gate_prices',
                    count(*) FROM fuelsignal.fuelsignal_silver.silver_terminal_gate_prices
                UNION ALL SELECT 'fuelsignal.fuelsignal_silver.silver_public_holidays',
                    count(*) FROM fuelsignal.fuelsignal_silver.silver_public_holidays
                UNION ALL SELECT 'fuelsignal.fuelsignal_silver.silver_data_quality_issues',
                    count(*) FROM fuelsignal.fuelsignal_silver.silver_data_quality_issues
                UNION ALL SELECT 'fuelsignal.fuelsignal_monitoring.monitoring_pipeline_runs',
                    count(*) FROM fuelsignal.fuelsignal_monitoring.monitoring_pipeline_runs
                UNION ALL SELECT 'fuelsignal.fuelsignal_monitoring.monitoring_source_freshness',
                    count(*) FROM fuelsignal.fuelsignal_monitoring.monitoring_source_freshness
    """,
    "dq_by_rule": """
        SELECT rule_name, count(*) rejected
        FROM fuelsignal.fuelsignal_silver.silver_data_quality_issues
        GROUP BY rule_name
        ORDER BY rule_name
    """,
    "invalid_fuelcheck_examples": """
        SELECT station_name, address, postcode, fuel_type, price, last_updated, raw_json
        FROM fuelsignal.fuelsignal_bronze.bronze_fuelcheck_prices_raw
        WHERE price IS NULL OR price < 80 OR price > 300
          OR try_cast(last_updated AS TIMESTAMP) IS NULL
        LIMIT 5
    """,
    "latest_audit": """
        SELECT pipeline_run_id, source_name, record_count, status, source_url
        FROM fuelsignal.fuelsignal_bronze.bronze_ingestion_audit
        ORDER BY ingestion_end_at DESC
        LIMIT 6
    """,
    "freshness": """
        SELECT source_name, last_ingestion_at, hours_since_last_ingestion,
          is_stale, record_count_last_run
        FROM fuelsignal.fuelsignal_monitoring.monitoring_source_freshness
        ORDER BY source_name
    """,
    "latest_runs": """
        SELECT run_id, pipeline_name, status, records_read, records_written,
          records_quarantined
        FROM fuelsignal.fuelsignal_monitoring.monitoring_pipeline_runs
        ORDER BY completed_at DESC
        LIMIT 6
    """,
}


def main() -> None:
    """Execute validation queries through the existing OAuth/PAT authentication."""
    host, token = databricks_auth()
    client = DatabricksSqlClient(host, token)
    output = {}
    for name, query in QUERIES.items():
        result = client.execute(query)
        output[name] = result.get("result", {}).get("data_array", [])
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
