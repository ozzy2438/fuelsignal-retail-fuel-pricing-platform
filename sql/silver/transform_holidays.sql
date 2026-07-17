-- Silver transformation: Public holidays
-- Bronze -> Silver with type casting and deduplication

INSERT INTO {silver_schema}.silver_public_holidays
SELECT DISTINCT
    CAST(date AS DATE) AS holiday_date,
    TRIM(holiday_name) AS holiday_name,
    COALESCE(state, 'NSW') AS state,
    is_national,
    YEAR(CAST(date AS DATE)) AS year,
    DAYOFWEEK(CAST(date AS DATE)) AS day_of_week,
    _source_name AS source_name,
    _pipeline_run_id
FROM {bronze_schema}.bronze_public_holidays_raw
WHERE CAST(date AS DATE) IS NOT NULL
  AND holiday_name IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM {silver_schema}.silver_public_holidays t
    WHERE t.holiday_date = CAST(date AS DATE)
      AND t.holiday_name = TRIM(holiday_name)
  );
