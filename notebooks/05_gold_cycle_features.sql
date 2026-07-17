-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 05 - Gold Cycle Features (SQL Window Functions)
-- MAGIC 
-- MAGIC **Purpose:** Build model-ready Gold layer tables using SQL window functions.
-- MAGIC 
-- MAGIC This notebook demonstrates the Silver-to-Gold transformation using:
-- MAGIC - Window functions for rolling aggregates
-- MAGIC - Days-since-event logic
-- MAGIC - Competitor positioning calculations
-- MAGIC - Margin computation

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. Gold: Station Daily Market Position

-- COMMAND ----------

CREATE OR REPLACE TEMP VIEW v_station_daily_market AS
WITH daily_prices AS (
    SELECT 
        fp.station_id,
        fp.fuel_type,
        fp.observed_date AS market_date,
        AVG(fp.price_cpl) AS station_price_cpl
    FROM ${catalog}.${schema_prefix}_silver.silver_fuel_prices fp
    GROUP BY fp.station_id, fp.fuel_type, fp.observed_date
),
competitor_prices AS (
    SELECT 
        dp.station_id,
        dp.fuel_type,
        dp.market_date,
        dp.station_price_cpl,
        -- Market-wide statistics
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY all_prices.station_price_cpl) 
            OVER (PARTITION BY dp.fuel_type, dp.market_date) AS market_median_price,
        -- Local competitor statistics (within 5km)
        AVG(comp_prices.station_price_cpl) 
            OVER (PARTITION BY dp.station_id, dp.fuel_type, dp.market_date) AS local_competitor_median_price,
        MIN(comp_prices.station_price_cpl) 
            OVER (PARTITION BY dp.station_id, dp.fuel_type, dp.market_date) AS local_competitor_min_price,
        MAX(comp_prices.station_price_cpl) 
            OVER (PARTITION BY dp.station_id, dp.fuel_type, dp.market_date) AS local_competitor_max_price,
        COUNT(comp_prices.station_price_cpl) 
            OVER (PARTITION BY dp.station_id, dp.fuel_type, dp.market_date) AS competitor_count
    FROM daily_prices dp
    LEFT JOIN ${catalog}.${schema_prefix}_silver.silver_competitor_pairs cp
        ON dp.station_id = cp.station_id
    LEFT JOIN daily_prices comp_prices
        ON cp.competitor_station_id = comp_prices.station_id
        AND dp.fuel_type = comp_prices.fuel_type
        AND dp.market_date = comp_prices.market_date
    LEFT JOIN daily_prices all_prices
        ON dp.fuel_type = all_prices.fuel_type
        AND dp.market_date = all_prices.market_date
)
SELECT 
    station_id,
    fuel_type,
    market_date,
    station_price_cpl,
    market_median_price,
    local_competitor_median_price,
    local_competitor_min_price,
    local_competitor_max_price,
    -- Price percentile within local market
    PERCENT_RANK() OVER (
        PARTITION BY fuel_type, market_date 
        ORDER BY station_price_cpl
    ) AS station_price_percentile,
    -- Difference from local median
    station_price_cpl - local_competitor_median_price AS price_vs_local_median_cpl,
    competitor_count
FROM competitor_prices;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. Gold: Market Cycle Features
-- MAGIC 
-- MAGIC Uses SQL window functions for rolling calculations and
-- MAGIC days-since-last-jump detection.

-- COMMAND ----------

CREATE OR REPLACE TEMP VIEW v_market_cycle_features AS
WITH daily_station_prices AS (
    SELECT 
        station_id,
        fuel_type,
        observed_date AS market_date,
        AVG(price_cpl) AS price_cpl,
        DAYOFWEEK(observed_date) AS day_of_week
    FROM ${catalog}.${schema_prefix}_silver.silver_fuel_prices
    GROUP BY station_id, fuel_type, observed_date
),
rolling_features AS (
    SELECT
        station_id,
        fuel_type,
        market_date,
        price_cpl,
        day_of_week,
        -- Rolling 7-day window functions
        MIN(price_cpl) OVER (
            PARTITION BY station_id, fuel_type 
            ORDER BY market_date 
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS rolling_7d_min_price,
        MAX(price_cpl) OVER (
            PARTITION BY station_id, fuel_type 
            ORDER BY market_date 
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS rolling_7d_max_price,
        AVG(price_cpl) OVER (
            PARTITION BY station_id, fuel_type 
            ORDER BY market_date 
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS rolling_7d_avg_price,
        -- 14-day price change
        price_cpl - LAG(price_cpl, 14) OVER (
            PARTITION BY station_id, fuel_type 
            ORDER BY market_date
        ) AS rolling_14d_price_change,
        -- Volatility (stddev over 14 days)
        STDDEV(price_cpl) OVER (
            PARTITION BY station_id, fuel_type 
            ORDER BY market_date 
            ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
        ) AS rolling_14d_volatility,
        -- Detect price jumps (>5 cpl increase from previous day)
        CASE 
            WHEN price_cpl - LAG(price_cpl, 1) OVER (
                PARTITION BY station_id, fuel_type ORDER BY market_date
            ) >= 5.0 THEN 1
            ELSE 0
        END AS is_jump_day
    FROM daily_station_prices
),
jump_tracking AS (
    SELECT
        *,
        -- Running sum of jumps to create groups
        SUM(is_jump_day) OVER (
            PARTITION BY station_id, fuel_type 
            ORDER BY market_date 
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS jump_group
    FROM rolling_features
)
SELECT
    station_id,
    fuel_type,
    market_date,
    rolling_7d_min_price,
    rolling_7d_max_price,
    rolling_7d_avg_price,
    rolling_14d_price_change,
    rolling_14d_volatility,
    -- Days since last jump: row number within each jump group
    ROW_NUMBER() OVER (
        PARTITION BY station_id, fuel_type, jump_group 
        ORDER BY market_date
    ) - 1 AS days_since_last_jump,
    -- Days since trough (minimum in current cycle)
    NULL AS days_since_last_trough,  -- Requires more complex logic
    -- Cycle position estimate
    CASE
        WHEN is_jump_day = 1 THEN 'PEAK'
        WHEN rolling_14d_price_change < -5.0 THEN 'DECLINING'
        WHEN price_cpl <= rolling_7d_min_price * 1.01 THEN 'TROUGH'
        ELSE 'MID_CYCLE'
    END AS cycle_position_estimate,
    day_of_week
FROM jump_tracking;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. Gold: Indicative Margin

-- COMMAND ----------

CREATE OR REPLACE TEMP VIEW v_indicative_margin AS
WITH daily_prices AS (
    SELECT 
        station_id,
        fuel_type,
        observed_date AS market_date,
        AVG(price_cpl) AS retail_price_cpl
    FROM ${catalog}.${schema_prefix}_silver.silver_fuel_prices
    GROUP BY station_id, fuel_type, observed_date
),
tgp_matched AS (
    SELECT 
        dp.station_id,
        dp.fuel_type,
        dp.market_date,
        dp.retail_price_cpl,
        tgp.tgp_cpl,
        dp.retail_price_cpl - tgp.tgp_cpl AS indicative_margin_cpl
    FROM daily_prices dp
    LEFT JOIN ${catalog}.${schema_prefix}_silver.silver_terminal_gate_prices tgp
        ON dp.fuel_type = tgp.fuel_type
        AND dp.market_date = tgp.tgp_date
        AND tgp.city = 'Sydney'  -- NSW focus
)
SELECT
    station_id,
    fuel_type,
    market_date,
    retail_price_cpl,
    tgp_cpl,
    indicative_margin_cpl,
    -- Margin vs 7-day average
    indicative_margin_cpl - AVG(indicative_margin_cpl) OVER (
        PARTITION BY station_id, fuel_type 
        ORDER BY market_date 
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS margin_vs_7d_avg,
    -- Margin percentile over 30 days
    PERCENT_RANK() OVER (
        PARTITION BY station_id, fuel_type 
        ORDER BY indicative_margin_cpl
        -- Note: RANGE not available, using ROWS approximation
    ) AS margin_percentile_30d
FROM tgp_matched
WHERE tgp_cpl IS NOT NULL;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. Gold: Combined Daily Pricing Inputs
-- MAGIC 
-- MAGIC Joins all features into a single model-ready table.

-- COMMAND ----------

CREATE OR REPLACE TEMP VIEW v_daily_pricing_inputs AS
SELECT
    m.station_id,
    m.fuel_type,
    m.market_date,
    m.station_price_cpl,
    m.market_median_price,
    m.local_competitor_median_price,
    m.local_competitor_min_price,
    m.local_competitor_max_price,
    m.station_price_percentile,
    m.price_vs_local_median_cpl,
    mg.retail_price_cpl,
    mg.tgp_cpl,
    mg.indicative_margin_cpl,
    c.days_since_last_jump,
    c.rolling_7d_min_price,
    c.rolling_7d_max_price,
    c.rolling_14d_price_change,
    c.day_of_week,
    CASE 
        WHEN h.holiday_date IS NOT NULL THEN TRUE 
        ELSE FALSE 
    END AS is_public_holiday,
    m.competitor_count
FROM v_station_daily_market m
LEFT JOIN v_market_cycle_features c
    ON m.station_id = c.station_id
    AND m.fuel_type = c.fuel_type
    AND m.market_date = c.market_date
LEFT JOIN v_indicative_margin mg
    ON m.station_id = mg.station_id
    AND m.fuel_type = mg.fuel_type
    AND m.market_date = mg.market_date
LEFT JOIN ${catalog}.${schema_prefix}_silver.silver_public_holidays h
    ON m.market_date = h.holiday_date;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 5. Write Gold Tables
-- MAGIC 
-- MAGIC Materialize the views into Delta tables.

-- COMMAND ----------

-- Note: In production, use INSERT OVERWRITE with partition pruning
-- For initial setup, we materialize the full views

-- This would be executed via PySpark:
-- spark.sql("SELECT * FROM v_daily_pricing_inputs").write.format("delta").mode("overwrite").saveAsTable(...)

SELECT 'Gold SQL transformations defined successfully' AS status;
