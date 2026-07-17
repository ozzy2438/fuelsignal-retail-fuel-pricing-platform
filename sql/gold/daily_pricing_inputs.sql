-- Gold: Daily pricing inputs with window functions
-- Combines market position, cycle features, margin, and calendar features

INSERT OVERWRITE {gold_schema}.gold_daily_pricing_inputs
WITH daily_prices AS (
    SELECT 
        station_id,
        fuel_type,
        observed_date AS market_date,
        AVG(price_cpl) AS station_price_cpl
    FROM {silver_schema}.silver_fuel_prices
    GROUP BY station_id, fuel_type, observed_date
),
market_stats AS (
    SELECT
        station_id,
        fuel_type,
        market_date,
        station_price_cpl,
        -- Market-wide median
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY station_price_cpl)
            OVER (PARTITION BY fuel_type, market_date) AS market_median_price,
        -- Price percentile
        PERCENT_RANK() OVER (
            PARTITION BY fuel_type, market_date ORDER BY station_price_cpl
        ) AS station_price_percentile
    FROM daily_prices
),
rolling_calc AS (
    SELECT
        station_id,
        fuel_type,
        market_date,
        station_price_cpl,
        -- 7-day rolling
        MIN(station_price_cpl) OVER (w7) AS rolling_7d_min_price,
        MAX(station_price_cpl) OVER (w7) AS rolling_7d_max_price,
        -- 14-day change
        station_price_cpl - LAG(station_price_cpl, 14) OVER (w_all) AS rolling_14d_price_change,
        -- Days since jump
        CASE 
            WHEN station_price_cpl - LAG(station_price_cpl, 1) OVER (w_all) >= 5.0 THEN 0
            ELSE NULL
        END AS jump_marker
    FROM daily_prices
    WINDOW 
        w7 AS (PARTITION BY station_id, fuel_type ORDER BY market_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
        w_all AS (PARTITION BY station_id, fuel_type ORDER BY market_date)
)
SELECT
    ms.station_id,
    ms.fuel_type,
    ms.market_date,
    ms.station_price_cpl,
    ms.market_median_price,
    NULL AS local_competitor_median_price,
    NULL AS local_competitor_min_price,
    NULL AS local_competitor_max_price,
    ms.station_price_percentile,
    ms.station_price_cpl - ms.market_median_price AS price_vs_local_median_cpl,
    tgp.tgp_cpl,
    ms.station_price_cpl - tgp.tgp_cpl AS indicative_margin_cpl,
    NULL AS days_since_last_jump,
    rc.rolling_7d_min_price,
    rc.rolling_7d_max_price,
    rc.rolling_14d_price_change,
    DAYOFWEEK(ms.market_date) AS day_of_week,
    CASE WHEN h.holiday_date IS NOT NULL THEN TRUE ELSE FALSE END AS is_public_holiday,
    NULL AS competitor_count
FROM market_stats ms
LEFT JOIN rolling_calc rc
    ON ms.station_id = rc.station_id
    AND ms.fuel_type = rc.fuel_type
    AND ms.market_date = rc.market_date
LEFT JOIN {silver_schema}.silver_terminal_gate_prices tgp
    ON ms.fuel_type = tgp.fuel_type
    AND ms.market_date = tgp.tgp_date
    AND tgp.city = 'Sydney'
LEFT JOIN {silver_schema}.silver_public_holidays h
    ON ms.market_date = h.holiday_date;
