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
    "monitoring_pricing_policy_recommendations": """
        CREATE TABLE IF NOT EXISTS {schema}.monitoring_pricing_policy_recommendations (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            policy_mode STRING,
            action STRING NOT NULL,
            reason STRING,
            guardrail_triggered BOOLEAN,
            jump_signal_used BOOLEAN,
            forecast_signal_used BOOLEAN,
            jump_probability DOUBLE,
            jump_threshold DOUBLE,
            forecast_3d_change_cpl DOUBLE,
            forecast_7d_change_cpl DOUBLE,
            station_vs_competitor_median_cpl DOUBLE,
            current_price_cpl DOUBLE,
            tgp_cpl DOUBLE,
            actual_indicative_margin_cpl DOUBLE,
            hypothetical_price_cpl DOUBLE,
            hypothetical_margin_cpl DOUBLE,
            margin_difference_cpl DOUBLE,
            days_since_price_change INT,
            is_stale_actual BOOLEAN,
            priced_above_competitors_actual BOOLEAN,
            baseline_action STRING,
            code_version STRING,
            backtest_run_id STRING,
            _pipeline_run_id STRING,
            ingested_at TIMESTAMP
        )
        USING DELTA
        COMMENT 'Per station-fuel-day HOLD/FOLLOW/LEAD policy recommendation - backtest output'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'monitoring'
        )
    """,
    "monitoring_policy_backtest_summary": """
        CREATE TABLE IF NOT EXISTS {schema}.monitoring_policy_backtest_summary (
            backtest_run_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            policy_mode STRING,
            hold_count LONG,
            follow_count LONG,
            lead_count LONG,
            baseline_hold_count LONG,
            guardrail_intervention_count LONG,
            stale_price_days_policy LONG,
            stale_price_days_baseline LONG,
            days_priced_above_competitors_actual LONG,
            days_priced_above_competitors_unaddressed LONG,
            avg_margin_difference_cpl DOUBLE,
            total_margin_difference_cpl DOUBLE,
            jump_signal_contribution_count LONG,
            forecast_signal_contribution_count LONG,
            row_count LONG,
            backtest_start_date DATE,
            backtest_end_date DATE,
            model_train_end_date DATE,
            generated_at TIMESTAMP,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Pricing-policy backtest results per fuel type, policy vs always-HOLD baseline'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'monitoring'
        )
    """,
}


def get_monitoring_ddl(schema: str) -> dict[str, str]:
    """Get all Monitoring DDL statements with schema name applied."""
    return {name: ddl.format(schema=schema) for name, ddl in MONITORING_SCHEMAS.items()}
