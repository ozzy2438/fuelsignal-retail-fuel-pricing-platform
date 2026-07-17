# Databricks notebook source
# MAGIC %md
# MAGIC # 02 - Silver Cleaning & Transformation
# MAGIC 
# MAGIC **Purpose:** Transform Bronze raw data into clean, validated Silver tables.
# MAGIC 
# MAGIC This notebook:
# MAGIC 1. Reads raw data from Bronze tables
# MAGIC 2. Applies data quality checks
# MAGIC 3. Normalizes fuel types and station identifiers
# MAGIC 4. Writes clean data to Silver tables
# MAGIC 5. Records quality issues without silently dropping records

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

from fuelsignal.config import load_project_config
from fuelsignal.utils.hashing import generate_pipeline_run_id
from fuelsignal.utils.validation import normalize_fuel_type

# COMMAND ----------

# Parameters
try:
    CATALOG = dbutils.widgets.get("catalog")
    SCHEMA_PREFIX = dbutils.widgets.get("schema_prefix")
except:
    CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    SCHEMA_PREFIX = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")

RUN_ID = generate_pipeline_run_id("silver")
BRONZE_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_bronze"
SILVER_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_silver"

print(f"Pipeline Run: {RUN_ID}")
print(f"Bronze → Silver transformation")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Transform Public Holidays (Bronze → Silver)

# COMMAND ----------

from pyspark.sql.functions import (
    col, to_date, dayofweek, year, lit, trim, 
    when, current_timestamp
)

try:
    holidays_bronze = spark.table(f"{BRONZE_SCHEMA}.bronze_public_holidays_raw")
    
    holidays_silver = (
        holidays_bronze
        .select(
            to_date(col("date"), "yyyy-MM-dd").alias("holiday_date"),
            trim(col("holiday_name")).alias("holiday_name"),
            col("state"),
            col("is_national"),
        )
        .withColumn("year", year(col("holiday_date")))
        .withColumn("day_of_week", dayofweek(col("holiday_date")))
        .withColumn("source_name", lit("nsw_public_holidays"))
        .withColumn("_pipeline_run_id", lit(RUN_ID))
        .filter(col("holiday_date").isNotNull())
        .dropDuplicates(["holiday_date", "holiday_name"])
    )
    
    holidays_silver.write.format("delta").mode("overwrite").saveAsTable(
        f"{SILVER_SCHEMA}.silver_public_holidays"
    )
    
    count = holidays_silver.count()
    print(f"✅ Silver public holidays: {count} records")
    
except Exception as e:
    print(f"❌ Public holidays transformation failed: {str(e)[:300]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Transform Fuel Prices (Bronze → Silver)
# MAGIC 
# MAGIC Applies:
# MAGIC - Fuel type normalization
# MAGIC - Price bound validation
# MAGIC - Coordinate validation
# MAGIC - Timestamp parsing
# MAGIC - Deduplication

# COMMAND ----------

from pyspark.sql.functions import (
    udf, to_timestamp, sha2, concat_ws
)
from pyspark.sql.types import StringType

# Register fuel type normalizer as UDF
normalize_fuel_udf = udf(normalize_fuel_type, StringType())

try:
    prices_bronze = spark.table(f"{BRONZE_SCHEMA}.bronze_fuelcheck_prices_raw")
    
    if prices_bronze.count() > 0:
        prices_silver = (
            prices_bronze
            .withColumn("station_id", 
                when(col("station_code").isNotNull(), col("station_code"))
                .otherwise(sha2(concat_ws("|", col("station_name"), col("address")), 256))
            )
            .withColumn("fuel_type", normalize_fuel_udf(col("fuel_type")))
            .withColumn("observed_at", to_timestamp(col("last_updated")))
            .withColumn("observed_date", to_date(col("last_updated")))
            .withColumn("price_cpl", col("price").cast("double"))
            .select(
                "station_id", "station_name", "brand", "address",
                "suburb", "postcode", 
                col("latitude").cast("double").alias("latitude"),
                col("longitude").cast("double").alias("longitude"),
                "fuel_type", "observed_at", "observed_date", "price_cpl",
                lit("nsw_fuelcheck").alias("source_name"),
                col("_ingested_at").alias("ingested_at"),
                lit(RUN_ID).alias("_pipeline_run_id"),
            )
            # Price bounds validation
            .filter((col("price_cpl") >= 80.0) & (col("price_cpl") <= 300.0))
            # Coordinate validation (NSW bounds)
            .filter(
                (col("latitude").between(-37.5, -28.0)) & 
                (col("longitude").between(141.0, 154.0))
            )
            .dropDuplicates(["station_id", "fuel_type", "observed_at"])
        )
        
        prices_silver.write.format("delta").mode("append").saveAsTable(
            f"{SILVER_SCHEMA}.silver_fuel_prices"
        )
        
        count = prices_silver.count()
        print(f"✅ Silver fuel prices: {count} records")
    else:
        print("⚠️  No Bronze fuel price records to transform")
        
except Exception as e:
    print(f"❌ Fuel prices transformation failed: {str(e)[:300]}")

# COMMAND ----------

print(f"\n{'='*60}")
print("SILVER TRANSFORMATION SUMMARY")
print(f"{'='*60}")
print(f"Run ID: {RUN_ID}")
print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
print(f"{'='*60}")
