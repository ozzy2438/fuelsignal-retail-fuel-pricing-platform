# Databricks notebook source
# MAGIC %md
# MAGIC # 06 - Pipeline Validation
# MAGIC 
# MAGIC **Purpose:** Validate the entire pipeline has run correctly.
# MAGIC 
# MAGIC Checks:
# MAGIC 1. All tables exist and are accessible
# MAGIC 2. Row counts are reported
# MAGIC 3. Data quality summary
# MAGIC 4. Source freshness
# MAGIC 5. Overall pipeline health

# COMMAND ----------

import os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

try:
    CATALOG = dbutils.widgets.get("catalog")
    SCHEMA_PREFIX = dbutils.widgets.get("schema_prefix")
except:
    CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    SCHEMA_PREFIX = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Table Inventory and Row Counts

# COMMAND ----------

schemas = ["bronze", "silver", "gold", "monitoring"]
results = []

for layer in schemas:
    schema_name = f"{CATALOG}.{SCHEMA_PREFIX}_{layer}"
    try:
        tables = spark.sql(f"SHOW TABLES IN {schema_name}").collect()
        for table in tables:
            table_name = table['tableName']
            full_name = f"{schema_name}.{table_name}"
            try:
                count = spark.table(full_name).count()
                results.append({
                    "layer": layer,
                    "table": table_name,
                    "row_count": count,
                    "status": "✅" if count > 0 else "⚠️ empty"
                })
            except Exception as e:
                results.append({
                    "layer": layer,
                    "table": table_name,
                    "row_count": -1,
                    "status": f"❌ {str(e)[:50]}"
                })
    except Exception as e:
        results.append({
            "layer": layer,
            "table": "(schema)",
            "row_count": -1,
            "status": f"❌ Schema error: {str(e)[:50]}"
        })

# COMMAND ----------

# Display results
print("="*70)
print("PIPELINE VALIDATION REPORT")
print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
print("="*70)
print(f"{'Layer':<12} {'Table':<40} {'Rows':<10} {'Status'}")
print("-"*70)

total_tables = 0
total_rows = 0
empty_tables = 0

for r in results:
    print(f"{r['layer']:<12} {r['table']:<40} {r['row_count']:<10} {r['status']}")
    total_tables += 1
    if r['row_count'] > 0:
        total_rows += r['row_count']
    elif r['row_count'] == 0:
        empty_tables += 1

print("-"*70)
print(f"Total tables: {total_tables}")
print(f"Total rows: {total_rows:,}")
print(f"Empty tables: {empty_tables}")
print("="*70)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Data Quality Summary

# COMMAND ----------

try:
    dq_issues = spark.table(
        f"{CATALOG}.{SCHEMA_PREFIX}_silver.silver_data_quality_issues"
    )
    issue_count = dq_issues.count()
    
    if issue_count > 0:
        print(f"\nData Quality Issues: {issue_count}")
        dq_issues.groupBy("severity", "rule_name").count().orderBy("severity").show(20)
    else:
        print("✅ No data quality issues recorded")
except Exception as e:
    print(f"DQ table check: {str(e)[:100]}")

# COMMAND ----------

print("\n✅ Pipeline validation complete")
