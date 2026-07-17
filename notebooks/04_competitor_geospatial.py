# Databricks notebook source
# MAGIC %md
# MAGIC # 04 - Competitor Geospatial Analysis
# MAGIC 
# MAGIC **Purpose:** Build station-to-station competitor pairs using Haversine distance.
# MAGIC 
# MAGIC Rule: Stations within ~5km are considered local competitors.
# MAGIC Uses PySpark for distributed cross-join and distance calculation.

# COMMAND ----------

import os, sys
from datetime import datetime, timezone, date
from pyspark.sql.functions import *
from pyspark.sql.types import DoubleType
import math

sys.path.insert(0, os.path.join(os.getcwd(), 'src'))
from fuelsignal.utils.hashing import generate_pipeline_run_id
from fuelsignal.utils.geo import haversine_distance_km

try:
    CATALOG = dbutils.widgets.get("catalog")
    SCHEMA_PREFIX = dbutils.widgets.get("schema_prefix")
except:
    CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    SCHEMA_PREFIX = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")

RUN_ID = generate_pipeline_run_id("competitor")
SILVER_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_silver"
COMPETITOR_RADIUS_KM = 5.0

# COMMAND ----------

# MAGIC %md
# MAGIC ## Register Haversine UDF

# COMMAND ----------

# Register as PySpark UDF for distributed computation
@udf(returnType=DoubleType())
def haversine_udf(lat1, lon1, lat2, lon2):
    """Haversine distance in km - PySpark UDF."""
    if any(v is None for v in [lat1, lon1, lat2, lon2]):
        return None
    
    R = 6371.0
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat/2)**2 + 
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build Competitor Pairs

# COMMAND ----------

try:
    stations = spark.table(f"{SILVER_SCHEMA}.silver_station_master")
    
    if stations.count() == 0:
        print("⚠️  No stations in master table. Run notebook 03 first.")
    else:
        # Self cross-join with alias
        s1 = stations.alias("s1").select(
            col("s1.station_id").alias("station_id"),
            col("s1.latitude").alias("lat1"),
            col("s1.longitude").alias("lon1"),
        )
        s2 = stations.alias("s2").select(
            col("s2.station_id").alias("competitor_station_id"),
            col("s2.latitude").alias("lat2"),
            col("s2.longitude").alias("lon2"),
        )
        
        # Cross join and compute distances
        pairs = (
            s1.crossJoin(s2)
            # Prevent self-pairs
            .filter(col("station_id") != col("competitor_station_id"))
            # Prevent duplicate reversed pairs (A-B and B-A)
            .filter(col("station_id") < col("competitor_station_id"))
            # Compute distance
            .withColumn("distance_km", 
                haversine_udf(col("lat1"), col("lon1"), col("lat2"), col("lon2"))
            )
            # Filter to within radius
            .filter(col("distance_km") <= COMPETITOR_RADIUS_KM)
            .select(
                "station_id",
                "competitor_station_id",
                round(col("distance_km"), 3).alias("distance_km"),
                lit(date.today()).alias("effective_from"),
                lit(None).cast("date").alias("effective_to"),
                lit("haversine").alias("calculation_method"),
                lit(RUN_ID).alias("_pipeline_run_id"),
            )
        )
        
        pairs.write.format("delta").mode("overwrite").saveAsTable(
            f"{SILVER_SCHEMA}.silver_competitor_pairs"
        )
        
        count = pairs.count()
        print(f"✅ Competitor pairs: {count} pairs within {COMPETITOR_RADIUS_KM}km")
        
        # Statistics
        print(f"\nDistance distribution:")
        pairs.select("distance_km").summary().show()
        
except Exception as e:
    print(f"❌ Competitor pair generation failed: {str(e)[:300]}")
