# Databricks notebook source
# MAGIC %md
# MAGIC # 01 - Bronze Ingestion
# MAGIC 
# MAGIC **Purpose:** Ingest raw data from official public sources into Bronze tables.
# MAGIC 
# MAGIC This notebook:
# MAGIC 1. Downloads data from NSW FuelCheck, AIP TGP, and public holidays
# MAGIC 2. Preserves raw source data with ingestion metadata
# MAGIC 3. Writes to Delta tables in the Bronze layer
# MAGIC 4. Records audit information for monitoring

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

from fuelsignal.config import load_project_config, load_sources_config
from fuelsignal.utils.hashing import generate_pipeline_run_id
from fuelsignal.ingestion.fuelcheck import FuelCheckIngester
from fuelsignal.ingestion.aip_tgp import AIPTerminalGatePriceIngester
from fuelsignal.ingestion.public_holidays import PublicHolidaysIngester

# COMMAND ----------

# Parameters
try:
    dbutils.widgets.text("environment", "dev", "Environment")
    dbutils.widgets.text("catalog", "main", "Catalog")
    dbutils.widgets.text("schema_prefix", "fuelsignal", "Schema Prefix")
    dbutils.widgets.text("run_date", "", "Run Date (YYYY-MM-DD, empty=today)")
    dbutils.widgets.dropdown("full_refresh", "false", ["true", "false"], "Full Refresh")
    
    ENVIRONMENT = dbutils.widgets.get("environment")
    CATALOG = dbutils.widgets.get("catalog")
    SCHEMA_PREFIX = dbutils.widgets.get("schema_prefix")
    FULL_REFRESH = dbutils.widgets.get("full_refresh") == "true"
except NameError:
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
    CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    SCHEMA_PREFIX = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")
    FULL_REFRESH = os.environ.get("FULL_REFRESH", "false").lower() == "true"

RUN_ID = generate_pipeline_run_id("bronze")
BRONZE_SCHEMA = f"{CATALOG}.{SCHEMA_PREFIX}_bronze"

print(f"Pipeline Run: {RUN_ID}")
print(f"Bronze Schema: {BRONZE_SCHEMA}")
print(f"Full Refresh: {FULL_REFRESH}")

if FULL_REFRESH:
    print("⚠️  FULL REFRESH mode - use with caution in production")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Ingest Public Holidays

# COMMAND ----------

try:
    holidays_ingester = PublicHolidaysIngester()
    holidays_data = holidays_ingester.fetch()
    holidays_records = holidays_ingester.to_raw_records(holidays_data)
    
    if holidays_records:
        holidays_df = spark.createDataFrame(holidays_records)
        holidays_df.write.format("delta").mode("append").saveAsTable(
            f"{BRONZE_SCHEMA}.bronze_public_holidays_raw"
        )
        print(f"✅ Public holidays: {len(holidays_records)} records written to Bronze")
    else:
        print("⚠️  No public holiday records to write")
        
except Exception as e:
    print(f"❌ Public holidays ingestion failed: {str(e)[:300]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Ingest FuelCheck Data

# COMMAND ----------

try:
    fuelcheck_ingester = FuelCheckIngester()
    fuelcheck_data = fuelcheck_ingester.fetch()
    fuelcheck_records = fuelcheck_ingester.to_raw_records(fuelcheck_data)
    
    print(f"FuelCheck fetch status: {fuelcheck_ingester.metadata['status']}")
    print(f"Resources found: {len(fuelcheck_data.get('resources_found', []))}")
    
    if fuelcheck_records:
        fuelcheck_df = spark.createDataFrame(fuelcheck_records)
        fuelcheck_df.write.format("delta").mode("append").saveAsTable(
            f"{BRONZE_SCHEMA}.bronze_fuelcheck_prices_raw"
        )
        print(f"✅ FuelCheck: {len(fuelcheck_records)} records written to Bronze")
    else:
        print("⚠️  No FuelCheck records available (may need API registration)")
        print(f"   Landing page: {fuelcheck_ingester.source_config.get('landing_page')}")
        
except Exception as e:
    print(f"❌ FuelCheck ingestion failed: {str(e)[:300]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Ingest AIP Terminal Gate Prices

# COMMAND ----------

try:
    tgp_ingester = AIPTerminalGatePriceIngester()
    tgp_data = tgp_ingester.fetch()
    tgp_records = tgp_ingester.to_raw_records(tgp_data)
    
    print(f"TGP fetch status: {tgp_ingester.metadata['status']}")
    print(f"HTML page size: {tgp_data.get('raw_html_length', 0)} chars")
    
    if tgp_records:
        # TGP records may include raw HTML snapshot
        print(f"✅ AIP TGP: {len(tgp_records)} records processed")
    else:
        print("⚠️  No structured TGP records extracted (HTML parsing may need refinement)")
        
except Exception as e:
    print(f"❌ AIP TGP ingestion failed: {str(e)[:300]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Write Audit Record

# COMMAND ----------

from pyspark.sql import Row

audit_records = []
for ingester_name, metadata in [
    ("public_holidays", holidays_ingester.metadata if 'holidays_ingester' in dir() else {}),
]:
    if metadata:
        audit_records.append(Row(
            pipeline_run_id=RUN_ID,
            source_name=metadata.get("source_name", ingester_name),
            source_url=metadata.get("source_url", ""),
            ingestion_start_at=datetime.now(timezone.utc),
            ingestion_end_at=datetime.now(timezone.utc),
            duration_seconds=metadata.get("retrieval_duration_seconds", 0.0),
            record_count=metadata.get("record_count", 0),
            status=metadata.get("status", "unknown"),
            error_message=None,
            environment=ENVIRONMENT,
            _ingested_at=datetime.now(timezone.utc),
        ))

if audit_records:
    audit_df = spark.createDataFrame(audit_records)
    audit_df.write.format("delta").mode("append").saveAsTable(
        f"{BRONZE_SCHEMA}.bronze_ingestion_audit"
    )
    print(f"✅ Audit records written: {len(audit_records)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

print("="*60)
print("BRONZE INGESTION SUMMARY")
print("="*60)
print(f"Run ID: {RUN_ID}")
print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
print(f"Schema: {BRONZE_SCHEMA}")
print("="*60)
