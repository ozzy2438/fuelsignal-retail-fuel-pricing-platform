"""Gold Layer Schema Definitions.

Defines DDL for Gold tables containing model-ready,
aggregated analytics data. Gold tables use SQL window functions
for rolling calculations and competitor positioning.
"""

GOLD_SCHEMAS = {
    "gold_station_daily_market": """
        CREATE TABLE IF NOT EXISTS {schema}.gold_station_daily_market (
            station_id STRING NOT NULL,
            fuel_type STRING NOT NULL,
            market_date DATE NOT NULL,
            station_price_cpl DOUBLE,
            market_median_price DOUBLE,
            local_competitor_median_price DOUBLE,
            local_competitor_min_price DOUBLE,
            local_competitor_max_price DOUBLE,
            station_price_percentile DOUBLE,
            price_vs_local_median_cpl DOUBLE,
            competitor_count INT,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Daily station market position vs competitors - gold layer'
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
            rolling_7d_avg_price DOUBLE,
            rolling_14d_price_change DOUBLE,
            rolling_14d_volatility DOUBLE,
            days_since_last_jump INT,
            days_since_last_trough INT,
            cycle_position_estimate STRING,
            day_of_week INT,
            is_public_holiday BOOLEAN,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Market cycle features for price-jump forecasting - gold layer'
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
        COMMENT 'Detailed competitor price positioning - gold layer'
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
            indicative_margin_cpl DOUBLE,
            margin_vs_7d_avg DOUBLE,
            margin_percentile_30d DOUBLE,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Indicative gross margin (retail minus TGP) - gold layer'
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
            station_price_cpl DOUBLE,
            market_median_price DOUBLE,
            local_competitor_median_price DOUBLE,
            local_competitor_min_price DOUBLE,
            local_competitor_max_price DOUBLE,
            station_price_percentile DOUBLE,
            price_vs_local_median_cpl DOUBLE,
            tgp_cpl DOUBLE,
            indicative_margin_cpl DOUBLE,
            days_since_last_jump INT,
            rolling_7d_min_price DOUBLE,
            rolling_7d_max_price DOUBLE,
            rolling_14d_price_change DOUBLE,
            day_of_week INT,
            is_public_holiday BOOLEAN,
            competitor_count INT,
            _pipeline_run_id STRING
        )
        USING DELTA
        COMMENT 'Combined model-ready daily pricing inputs - gold layer'
        TBLPROPERTIES (
            'delta.autoOptimize.optimizeWrite' = 'true',
            'quality' = 'gold'
        )
    """,
}


def get_gold_ddl(schema: str) -> dict[str, str]:
    """Get all Gold DDL statements with schema name applied."""
    return {
        name: ddl.format(schema=schema)
        for name, ddl in GOLD_SCHEMAS.items()
    }
