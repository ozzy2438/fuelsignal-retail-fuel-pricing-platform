-- FuelSignal Bronze DDL
-- Idempotent table creation for Bronze layer
-- Run with: spark.sql(open('sql/ddl/bronze.sql').read().format(schema=BRONZE_SCHEMA))

CREATE SCHEMA IF NOT EXISTS {schema};

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
COMMENT 'Raw NSW FuelCheck retail fuel prices';

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
COMMENT 'Raw NSW FuelCheck station metadata';

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
COMMENT 'Raw AIP Terminal Gate Prices';

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
COMMENT 'Raw NSW public holiday dates';

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
    _ingested_at TIMESTAMP
)
USING DELTA
COMMENT 'Bronze ingestion audit trail';
