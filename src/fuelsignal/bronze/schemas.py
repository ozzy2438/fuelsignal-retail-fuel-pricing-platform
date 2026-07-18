"""Bronze Layer Schema Definitions.

Defines the DDL for all Bronze tables in the FuelSignal platform.
Bronze tables store raw, immutable source data with ingestion metadata.

All Bronze tables include these standard metadata columns:
- _ingested_at: UTC timestamp of ingestion
- _source_name: Name of the data source
- _source_url: URL from which data was fetched
- _source_file: Specific file or resource identifier
- _source_record_hash: SHA-256 hash for deduplication
- _pipeline_run_id: Unique pipeline run identifier
"""

BRONZE_SCHEMAS = {
    "bronze_fuelcheck_prices_raw": """
        CREATE TABLE IF NOT EXISTS {schema}.bronze_fuelcheck_prices_raw (
            station_code STRING,
            station_name STRING,
            brand STRING,
            address STRING,
            suburb STRING,
            postcode STRING,
            latitude DOUBLE,
            longitude DOUBLE,
            fuel_type STRING,
            price DOUBLE,
            last_updated STRING,
            raw_json STRING,
            _ingested_at TIMESTAMP,
            _source_name STRING,
            _source_url STRING,
            _source_file STRING,
            _source_record_hash STRING,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Raw NSW FuelCheck retail fuel prices - immutable bronze layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'bronze'
        )
    """,
    "bronze_fuelcheck_stations_raw": """
        CREATE TABLE IF NOT EXISTS {schema}.bronze_fuelcheck_stations_raw (
            station_code STRING,
            station_name STRING,
            brand STRING,
            address STRING,
            suburb STRING,
            state STRING,
            postcode STRING,
            latitude DOUBLE,
            longitude DOUBLE,
            station_type STRING,
            raw_json STRING,
            _ingested_at TIMESTAMP,
            _source_name STRING,
            _source_url STRING,
            _source_file STRING,
            _source_record_hash STRING,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Raw NSW FuelCheck station metadata - immutable bronze layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'bronze'
        )
    """,
    "bronze_aip_tgp_raw": """
        CREATE TABLE IF NOT EXISTS {schema}.bronze_aip_tgp_raw (
            tgp_date STRING,
            terminal STRING,
            city STRING,
            product STRING,
            price_cpl STRING,
            raw_html_snippet STRING,
            _ingested_at TIMESTAMP,
            _source_name STRING,
            _source_url STRING,
            _source_file STRING,
            _source_record_hash STRING,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Raw AIP Terminal Gate Prices - immutable bronze layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'bronze'
        )
    """,
    "bronze_public_holidays_raw": """
        CREATE TABLE IF NOT EXISTS {schema}.bronze_public_holidays_raw (
            date STRING,
            holiday_name STRING,
            state STRING,
            is_national BOOLEAN,
            _ingested_at TIMESTAMP,
            _source_name STRING,
            _source_url STRING,
            _source_file STRING,
            _source_record_hash STRING,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Raw NSW public holiday dates - immutable bronze layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'bronze'
        )
    """,
    "bronze_ingestion_audit": """
        CREATE TABLE IF NOT EXISTS {schema}.bronze_ingestion_audit (
            pipeline_run_id STRING,
            source_name STRING,
            source_url STRING,
            ingestion_start_at TIMESTAMP,
            ingestion_end_at TIMESTAMP,
            duration_seconds DOUBLE,
            record_count LONG,
            status STRING,
            error_message STRING,
            environment STRING,
            _ingested_at TIMESTAMP,
            stage STRING,
            source_file STRING,
            source_checksum STRING,
            records_read LONG,
            records_written LONG,
            records_rejected LONG,
            source_date_range STRING,
            code_version STRING
        )
        USING DELTA
        COMMENT 'Audit trail for all bronze ingestion pipeline runs'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'monitoring'
        )
    """,
}


def get_bronze_ddl(schema: str) -> dict[str, str]:
    """Get all Bronze DDL statements with schema name applied.

    Args:
        schema: Fully qualified schema name (e.g., 'main.fuelsignal_bronze')

    Returns:
        Dictionary mapping table name to DDL statement.
    """
    return {name: ddl.format(schema=schema) for name, ddl in BRONZE_SCHEMAS.items()}
