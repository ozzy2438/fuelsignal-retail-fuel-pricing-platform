"""Monitoring Schema Definitions."""

MONITORING_SCHEMAS = {
    "monitoring_pipeline_runs": """
        CREATE TABLE IF NOT EXISTS {schema}.monitoring_pipeline_runs (
            run_id STRING NOT NULL,
            pipeline_name STRING NOT NULL,
            stage STRING NOT NULL,
            started_at TIMESTAMP NOT NULL,
            completed_at TIMESTAMP,
            duration_seconds DOUBLE,
            status STRING NOT NULL,
            records_read LONG,
            records_written LONG,
            records_quarantined LONG,
            error_message STRING,
            environment STRING,
            parameters STRING
        )
        USING DELTA
        COMMENT 'Pipeline execution audit trail'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'monitoring'
        )
    """,
    
    "monitoring_data_quality_results": """
        CREATE TABLE IF NOT EXISTS {schema}.monitoring_data_quality_results (
            run_id STRING NOT NULL,
            check_timestamp TIMESTAMP NOT NULL,
            table_name STRING NOT NULL,
            rule_name STRING NOT NULL,
            rule_description STRING,
            severity STRING,
            total_records LONG,
            passed_records LONG,
            failed_records LONG,
            pass_rate DOUBLE,
            threshold DOUBLE,
            status STRING,
            details STRING
        )
        USING DELTA
        COMMENT 'Data quality check results per pipeline run'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'monitoring'
        )
    """,
    
    "monitoring_source_freshness": """
        CREATE TABLE IF NOT EXISTS {schema}.monitoring_source_freshness (
            check_timestamp TIMESTAMP NOT NULL,
            source_name STRING NOT NULL,
            last_ingestion_at TIMESTAMP,
            hours_since_last_ingestion DOUBLE,
            expected_max_hours DOUBLE,
            is_stale BOOLEAN,
            alert_triggered BOOLEAN,
            record_count_last_run LONG
        )
        USING DELTA
        COMMENT 'Source data freshness monitoring'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'monitoring'
        )
    """,
}


def get_monitoring_ddl(schema: str) -> dict[str, str]:
    """Get all Monitoring DDL statements with schema name applied."""
    return {
        name: ddl.format(schema=schema)
        for name, ddl in MONITORING_SCHEMAS.items()
    }
