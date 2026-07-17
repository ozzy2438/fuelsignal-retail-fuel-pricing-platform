# Databricks notebook source
# MAGIC %md
# MAGIC # 00 - Environment Validation
# MAGIC 
# MAGIC **Purpose:** Validate Databricks workspace connectivity and environment setup.
# MAGIC 
# MAGIC This notebook:
# MAGIC 1. Validates authentication (without exposing secrets)
# MAGIC 2. Checks available catalogs and schemas
# MAGIC 3. Creates required schemas if they don't exist
# MAGIC 4. Creates all Delta tables using idempotent DDL
# MAGIC 5. Reports environment capabilities

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone

# Add project source to path
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

# Parameters (use Databricks widgets if available)
try:
    dbutils.widgets.text("environment", "dev", "Environment")
    dbutils.widgets.text("catalog", "main", "Catalog")
    dbutils.widgets.text("schema_prefix", "fuelsignal", "Schema Prefix")
    
    ENVIRONMENT = dbutils.widgets.get("environment")
    CATALOG = dbutils.widgets.get("catalog")
    SCHEMA_PREFIX = dbutils.widgets.get("schema_prefix")
except NameError:
    # Running outside Databricks
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
    CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    SCHEMA_PREFIX = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")

print(f"Environment: {ENVIRONMENT}")
print(f"Catalog: {CATALOG}")
print(f"Schema Prefix: {SCHEMA_PREFIX}")
print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Validate Authentication

# COMMAND ----------

# Validate connection without exposing secrets
try:
    result = spark.sql("SELECT current_user() as user, current_catalog() as catalog")
    user_info = result.collect()[0]
    print(f"✅ Authentication successful")
    print(f"   User: {user_info['user']}")
    print(f"   Current catalog: {user_info['catalog']}")
except Exception as e:
    error_msg = str(e)
    # Never print token-related info
    if "token" in error_msg.lower() or "auth" in error_msg.lower():
        print(f"❌ Authentication failed. Check DATABRICKS_HOST and DATABRICKS_TOKEN.")
    else:
        print(f"❌ Connection error: {error_msg[:200]}")
    raise SystemExit("Cannot proceed without valid authentication.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Check Workspace Capabilities

# COMMAND ----------

# Check Unity Catalog support
try:
    catalogs = spark.sql("SHOW CATALOGS").collect()
    catalog_names = [row['catalog'] for row in catalogs]
    print(f"✅ Unity Catalog available. Catalogs: {catalog_names}")
    UNITY_CATALOG_AVAILABLE = True
except Exception as e:
    print(f"⚠️  Unity Catalog not available: {str(e)[:100]}")
    print("   Falling back to hive_metastore")
    UNITY_CATALOG_AVAILABLE = False
    CATALOG = "hive_metastore"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Create Schemas

# COMMAND ----------

SCHEMAS = [
    f"{SCHEMA_PREFIX}_bronze",
    f"{SCHEMA_PREFIX}_silver",
    f"{SCHEMA_PREFIX}_gold",
    f"{SCHEMA_PREFIX}_monitoring",
]

for schema_name in SCHEMAS:
    full_schema = f"{CATALOG}.{schema_name}" if UNITY_CATALOG_AVAILABLE else schema_name
    try:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {full_schema}")
        print(f"✅ Schema ready: {full_schema}")
    except Exception as e:
        print(f"❌ Failed to create schema {full_schema}: {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Create Bronze Tables

# COMMAND ----------

from fuelsignal.bronze.schemas import get_bronze_ddl

bronze_schema = f"{CATALOG}.{SCHEMA_PREFIX}_bronze" if UNITY_CATALOG_AVAILABLE else f"{SCHEMA_PREFIX}_bronze"
bronze_ddl = get_bronze_ddl(bronze_schema)

for table_name, ddl in bronze_ddl.items():
    try:
        spark.sql(ddl)
        print(f"✅ Bronze table ready: {table_name}")
    except Exception as e:
        print(f"❌ Failed to create {table_name}: {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Create Silver Tables

# COMMAND ----------

from fuelsignal.silver.schemas import get_silver_ddl

silver_schema = f"{CATALOG}.{SCHEMA_PREFIX}_silver" if UNITY_CATALOG_AVAILABLE else f"{SCHEMA_PREFIX}_silver"
silver_ddl = get_silver_ddl(silver_schema)

for table_name, ddl in silver_ddl.items():
    try:
        spark.sql(ddl)
        print(f"✅ Silver table ready: {table_name}")
    except Exception as e:
        print(f"❌ Failed to create {table_name}: {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Create Gold Tables

# COMMAND ----------

from fuelsignal.gold.schemas import get_gold_ddl

gold_schema = f"{CATALOG}.{SCHEMA_PREFIX}_gold" if UNITY_CATALOG_AVAILABLE else f"{SCHEMA_PREFIX}_gold"
gold_ddl = get_gold_ddl(gold_schema)

for table_name, ddl in gold_ddl.items():
    try:
        spark.sql(ddl)
        print(f"✅ Gold table ready: {table_name}")
    except Exception as e:
        print(f"❌ Failed to create {table_name}: {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Create Monitoring Tables

# COMMAND ----------

from fuelsignal.monitoring import get_monitoring_ddl

monitoring_schema = f"{CATALOG}.{SCHEMA_PREFIX}_monitoring" if UNITY_CATALOG_AVAILABLE else f"{SCHEMA_PREFIX}_monitoring"
monitoring_ddl = get_monitoring_ddl(monitoring_schema)

for table_name, ddl in monitoring_ddl.items():
    try:
        spark.sql(ddl)
        print(f"✅ Monitoring table ready: {table_name}")
    except Exception as e:
        print(f"❌ Failed to create {table_name}: {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Validation Summary

# COMMAND ----------

print("="*60)
print("ENVIRONMENT VALIDATION SUMMARY")
print("="*60)
print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
print(f"Environment: {ENVIRONMENT}")
print(f"Catalog: {CATALOG}")
print(f"Unity Catalog: {'Available' if UNITY_CATALOG_AVAILABLE else 'Not Available'}")
print(f"Schemas created: {len(SCHEMAS)}")
print(f"Bronze tables: {len(bronze_ddl)}")
print(f"Silver tables: {len(silver_ddl)}")
print(f"Gold tables: {len(gold_ddl)}")
print(f"Monitoring tables: {len(monitoring_ddl)}")
print("="*60)
print("✅ Environment validation complete")
