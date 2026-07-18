"""Gold Layer Schema Definitions.

Defines DDL for Gold tables containing model-ready, aggregated analytics data.
Gold tables use SQL window functions for rolling calculations and competitor
positioning. See docs/feature-engineering.md and docs/jump-label-definition.md
for the grain, join rules, and leakage controls behind every column here.

Canonical grain for all per-station Gold tables: station_id x fuel_type x market_date.
gold_competitor_positioning additionally grains by competitor_station_id (one row per
competitor pair per day). gold_price_jump_labels grains by fuel_type x market_date only
(a market-wide event, not a per-station one) - see jump-label-definition.md.
"""

GOLD_SCHEMAS = {
    "gold_station_daily_market": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_station_daily_market (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            daily_open_price_cpl DOUBLE,
            daily_close_price_cpl DOUBLE,
            daily_min_price_cpl DOUBLE,
            daily_max_price_cpl DOUBLE,
            daily_observation_count INT,
            last_observed_at TIMESTAMP,
            market_median_price_cpl DOUBLE,
            price_vs_market_median_cpl DOUBLE,
            competitor_count INT,
            local_competitor_min_price_cpl DOUBLE,
            local_competitor_max_price_cpl DOUBLE,
            local_competitor_median_price_cpl DOUBLE,
            local_competitor_mean_price_cpl DOUBLE,
            station_vs_competitor_median_cpl DOUBLE,
            station_price_percentile DOUBLE,
            rank_within_local_market INT,
            _pipeline_run_id STRING,
            ingested_at TIMESTAMP
        )
        USING DELTA
        COMMENT 'Daily station price construction and local competitor positioning - gold layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
    "gold_market_cycle_features": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_market_cycle_features (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            rolling_7d_min_price DOUBLE,
            rolling_7d_max_price DOUBLE,
            rolling_7d_mean_price DOUBLE,
            rolling_7d_std_price DOUBLE,
            rolling_14d_min_price DOUBLE,
            rolling_14d_max_price DOUBLE,
            rolling_14d_price_change_cpl DOUBLE,
            days_since_local_minimum INT,
            days_since_last_detected_jump INT,
            price_position_within_14d_range DOUBLE,
            market_median_price_cpl DOUBLE,
            market_daily_change_cpl DOUBLE,
            tgp_7d_change_cpl DOUBLE,
            margin_compression_cpl DOUBLE,
            day_of_week INT,
            is_public_holiday BOOLEAN,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Trailing-only price-cycle features for jump forecasting - gold layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
    "gold_competitor_positioning": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_competitor_positioning (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            competitor_station_id STRING NOT NULL,
            competitor_brand STRING,
            distance_km DOUBLE,
            station_price_cpl DOUBLE,
            competitor_price_cpl DOUBLE,
            price_difference_cpl DOUBLE,
            is_cheapest_local BOOLEAN,
            rank_in_local_market INT,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Detailed per-competitor-pair daily price positioning - gold layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
    "gold_indicative_margin": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_indicative_margin (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            retail_price_cpl DOUBLE,
            tgp_cpl DOUBLE,
            tgp_match_type STRING,
            indicative_margin_cpl DOUBLE,
            price_tgp_spread_cpl DOUBLE,
            margin_vs_7d_avg_cpl DOUBLE,
            margin_percentile_30d DOUBLE,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Indicative margin (retail minus TGP), NOT a realised P&L margin - gold layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
    "gold_daily_pricing_inputs": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_daily_pricing_inputs (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            daily_close_price_cpl DOUBLE,
            market_median_price_cpl DOUBLE,
            local_competitor_median_price_cpl DOUBLE,
            local_competitor_min_price_cpl DOUBLE,
            local_competitor_max_price_cpl DOUBLE,
            station_price_percentile DOUBLE,
            station_vs_competitor_median_cpl DOUBLE,
            rank_within_local_market INT,
            competitor_count INT,
            tgp_cpl DOUBLE,
            indicative_margin_cpl DOUBLE,
            margin_compression_cpl DOUBLE,
            rolling_7d_min_price DOUBLE,
            rolling_7d_max_price DOUBLE,
            rolling_7d_mean_price DOUBLE,
            rolling_7d_std_price DOUBLE,
            rolling_14d_min_price DOUBLE,
            rolling_14d_max_price DOUBLE,
            rolling_14d_price_change_cpl DOUBLE,
            days_since_local_minimum INT,
            days_since_last_detected_jump INT,
            price_position_within_14d_range DOUBLE,
            tgp_7d_change_cpl DOUBLE,
            day_of_week INT,
            is_public_holiday BOOLEAN,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Combined model-ready daily FEATURE inputs (no labels) - gold layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
    "gold_price_jump_labels": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_price_jump_labels (
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            market_median_price_cpl DOUBLE,
            market_daily_change_cpl DOUBLE,
            jump_threshold_cpl DOUBLE,
            jump_today BOOLEAN,
            jump_within_24h BOOLEAN,
            jump_within_48h BOOLEAN,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Market-level price-jump TARGET labels - uses future info, never join as a feature'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
}


def get_gold_ddl(schema: str) -> dict[str, str]:
    """Get all Gold DDL statements with schema name applied."""
    return {name: ddl.format(schema=schema) for name, ddl in GOLD_SCHEMAS.items()}
