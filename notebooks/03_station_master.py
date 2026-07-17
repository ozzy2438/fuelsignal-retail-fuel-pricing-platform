# Databricks notebook source
# MAGIC %md
# MAGIC # 03 - Station Master
# MAGIC 
# MAGIC **Purpose:** Build and maintain the canonical station master table.
# MAGIC 
# MAGIC Creates a deterministic station key using official station codes where available,
# MAGIC with fallback to hash-based keys for stations without codes.

# COMMAND ----------

import os, sys
from datetime import datetime, timezone
from pyspark.sql.functions import *

sys.path.insert(0, os.path.join(os.getcwd(), 'src'))
from fuelsignal.utils.hashing import generate_pipeline_run_id

try:
    CATALOG = dbutils.widgets.get("catalog")
    SCHEMA_PREFIX = dbutils.widgets.get("schema_prefix")
except:
    CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    SCHEMA_PREFIX = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")

RUN_ID = generate_pipeline_run_id("station_master")
SILVER_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_silver"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build Station Master from Silver Fuel Prices

# COMMAND ----------

try:
    prices = spark.table(f"{SILVER_SCHEMA}.silver_fuel_prices")
    
    station_master = (
        prices
        .groupBy("station_id", "station_name", "brand", "address", 
                 "suburb", "postcode", "latitude", "longitude")
        .agg(
            min("observed_date").alias("first_seen_date"),
            max("observed_date").alias("last_seen_date"),
            lit("nsw_fuelcheck").alias("source_name"),
        )
        .withColumn("station_code", col("station_id"))
        .withColumn("brand_normalized", upper(trim(col("brand"))))
        .withColumn("state", lit("NSW"))
        .withColumn("is_active", lit(True))
        .withColumn("_pipeline_run_id", lit(RUN_ID))
        .select(
            "station_id", "station_code", "station_name", "brand",
            "brand_normalized", "address", "suburb", "postcode", "state",
            "latitude", "longitude", "is_active",
            "first_seen_date", "last_seen_date",
            "source_name", "_pipeline_run_id"
        )
    )
    
    station_master.write.format("delta").mode("overwrite").saveAsTable(
        f"{SILVER_SCHEMA}.silver_station_master"
    )
    
    count = station_master.count()
    print(f"✅ Station master: {count} unique stations")
    
    # Show brand distribution
    print("\nTop brands:")
    station_master.groupBy("brand_normalized").count().orderBy(desc("count")).show(10)
    
except Exception as e:
    print(f"❌ Station master build failed: {str(e)[:300]}")
