-- FuelSignal Silver DDL

CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.silver_fuel_prices (
    station_id STRING NOT NULL,
    station_name STRING,
    brand STRING,
    address STRING,
    suburb STRING,
    postcode STRING,
    latitude DOUBLE,
    longitude DOUBLE,
    fuel_type STRING NOT NULL,
    observed_at TIMESTAMP NOT NULL,
    observed_date DATE,
    price_cpl DOUBLE NOT NULL,
    source_name STRING,
    ingested_at TIMESTAMP,
    _pipeline_run_id STRING
)
USING DELTA
COMMENT 'Cleaned retail fuel prices';

CREATE TABLE IF NOT EXISTS {schema}.silver_station_master (
    station_id STRING NOT NULL,
    station_code STRING,
    station_name STRING NOT NULL,
    brand STRING NOT NULL,
    brand_normalized STRING,
    address STRING,
    suburb STRING,
    postcode STRING,
    state STRING,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    is_active BOOLEAN,
    first_seen_date DATE,
    last_seen_date DATE,
    source_name STRING,
    _pipeline_run_id STRING,
    official_station_code STRING,
    match_method STRING,
    match_confidence DOUBLE,
    effective_from DATE,
    effective_to DATE,
    ingested_at TIMESTAMP
)
USING DELTA
COMMENT 'Canonical station master';

CREATE TABLE IF NOT EXISTS {schema}.silver_terminal_gate_prices (
    tgp_date DATE NOT NULL,
    terminal STRING NOT NULL,
    city STRING NOT NULL,
    fuel_type STRING NOT NULL,
    tgp_cpl DOUBLE NOT NULL,
    source_name STRING,
    ingested_at TIMESTAMP,
    _pipeline_run_id STRING
)
USING DELTA
COMMENT 'Cleaned terminal gate prices';

CREATE TABLE IF NOT EXISTS {schema}.silver_public_holidays (
    holiday_date DATE NOT NULL,
    holiday_name STRING NOT NULL,
    state STRING NOT NULL,
    is_national BOOLEAN,
    year INT,
    day_of_week INT,
    source_name STRING,
    _pipeline_run_id STRING
)
USING DELTA
COMMENT 'NSW public holidays';

CREATE TABLE IF NOT EXISTS {schema}.silver_competitor_pairs (
    station_id STRING NOT NULL,
    competitor_station_id STRING NOT NULL,
    distance_km DOUBLE NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    calculation_method STRING,
    _pipeline_run_id STRING,
    created_at TIMESTAMP
)
USING DELTA
COMMENT 'Station competitor pairs within 5km';

CREATE TABLE IF NOT EXISTS {schema}.silver_data_quality_issues (
    issue_id STRING NOT NULL,
    pipeline_run_id STRING NOT NULL,
    source_table STRING NOT NULL,
    target_table STRING NOT NULL,
    rule_name STRING NOT NULL,
    severity STRING NOT NULL,
    column_name STRING,
    record_identifier STRING,
    issue_description STRING,
    raw_value STRING,
    action_taken STRING,
    detected_at TIMESTAMP NOT NULL
)
USING DELTA
COMMENT 'Data quality issues and quarantined records';
