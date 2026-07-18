"""Run the live, idempotent FuelSignal Bronze and first Silver pipeline."""

# ruff: noqa: E501, S603, S608

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from deploy_databricks_foundation import (  # noqa: E402
    DatabricksSqlClient,
    DeploymentError,
    validate_identifier,
)
from fuelsignal.config import (  # noqa: E402
    load_project_config,
    load_sources_config,
)

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"
RAW_VOLUME = "raw_sources"
USER_AGENT = "FuelSignal-Portfolio/0.2 (+https://github.com/ozzy2438/fuelsignal-retail-fuel-pricing-platform)"
DEFAULT_HOST = "https://dbc-aaefb4e4-e074.cloud.databricks.com"


def sql_literal(value: str | None) -> str:
    """Return a safely quoted SQL string literal."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def sha256_bytes(content: bytes) -> str:
    """Return a deterministic SHA-256 artifact hash."""
    return hashlib.sha256(content).hexdigest()


def fetch_required(url: str, timeout: int, retries: int) -> tuple[bytes, float]:
    """Download a required official source or fail the pipeline."""
    started = time.monotonic()
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            if not response.content:
                raise RuntimeError("source returned an empty response")
            return response.content, time.monotonic() - started
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 10))
    raise RuntimeError(f"Required source unavailable: {url}: {last_error}") from last_error


def databricks_auth() -> tuple[str, str]:
    """Load PAT environment variables or a short-lived Databricks CLI OAuth token."""
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if host and token:
        return host.rstrip("/"), token

    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE", "fuelsignal")
    cli = Path.home() / ".local" / "bin" / "databricks"
    if not cli.exists():
        raise OSError(
            "Databricks credentials are unavailable: set DATABRICKS_HOST and "
            "DATABRICKS_TOKEN or install/login with the Databricks CLI"
        )
    process = subprocess.run(
        [str(cli), "auth", "token", "--profile", profile],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(process.stdout)
    access_token = payload.get("access_token")
    if not access_token:
        raise OSError(f"Databricks CLI profile '{profile}' returned no access token")
    return os.environ.get("DATABRICKS_HOST", DEFAULT_HOST).rstrip("/"), access_token


def parse_aip_workbook(content: bytes) -> pd.DataFrame:
    """Convert every official AIP city/date/product value to a staging row."""
    rows: list[dict[str, Any]] = []
    for sheet_name, product in (("Petrol TGP", "ULP"), ("Diesel TGP", "Diesel")):
        frame = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name)
        date_column = frame.columns[0]
        city_columns = [
            column for column in frame.columns[1:] if "average" not in str(column).lower()
        ]
        for _, source_row in frame.iterrows():
            tgp_date = pd.to_datetime(source_row[date_column], errors="coerce")
            if pd.isna(tgp_date):
                continue
            for city in city_columns:
                value = pd.to_numeric(source_row[city], errors="coerce")
                rows.append(
                    {
                        "tgp_date": tgp_date.date().isoformat(),
                        "terminal": str(city).strip(),
                        "city": str(city).strip(),
                        "product": product,
                        "price_cpl": None if pd.isna(value) else float(value),
                        "raw_json": json.dumps(
                            {
                                "date": tgp_date.date().isoformat(),
                                "location": str(city).strip(),
                                "product": product,
                                "price_cpl": None if pd.isna(value) else float(value),
                            },
                            sort_keys=True,
                        ),
                    }
                )
    if not rows:
        raise RuntimeError("AIP workbook contained no TGP records")
    return pd.DataFrame(rows)


def clean_holiday_name(value: str) -> str:
    """Remove NSW page footnote markers from a holiday name."""
    return re.sub(r"^\d+", "", str(value)).strip()


def parse_holiday_page(content: bytes) -> pd.DataFrame:
    """Parse the official NSW 2026-2027 holiday HTML table."""
    tables = pd.read_html(io.StringIO(content.decode("utf-8")))
    candidates = [table for table in tables if "Holiday" in table.columns]
    if not candidates:
        raise RuntimeError("NSW holiday page contained no expected holiday table")

    rows: list[dict[str, Any]] = []
    frame = candidates[0]
    for _, source_row in frame.iterrows():
        holiday_name = clean_holiday_name(source_row["Holiday"])
        for year in (2026, 2027):
            raw_date = str(
                source_row[str(year)] if str(year) in frame.columns else source_row[year]
            )
            if raw_date == "Not applicable":
                continue
            parsed_date = pd.to_datetime(raw_date, errors="coerce")
            rows.append(
                {
                    "date": None if pd.isna(parsed_date) else parsed_date.date().isoformat(),
                    "holiday_name": holiday_name,
                    "state": "NSW",
                    "is_national": holiday_name
                    in {
                        "New Year's Day",
                        "Australia Day",
                        "Good Friday",
                        "Easter Monday",
                        "Anzac Day",
                        "Christmas Day",
                        "Boxing Day",
                    },
                    "raw_json": json.dumps(
                        {"holiday": str(source_row["Holiday"]), "date_text": raw_date},
                        sort_keys=True,
                    ),
                }
            )
    if not rows:
        raise RuntimeError("NSW holiday page contained no holiday records")
    return pd.DataFrame(rows)


def dataframe_csv_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize a staging frame without changing its logical values."""
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, quoting=csv.QUOTE_MINIMAL)
    return buffer.getvalue().encode("utf-8")


def dataframe_jsonl_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize a staging frame as JSON Lines for robust Databricks ingestion."""
    return frame.to_json(orient="records", lines=True).encode("utf-8")


class LivePipeline:
    """Orchestrate official downloads and Databricks Bronze/Silver SQL."""

    def __init__(self, client: DatabricksSqlClient, run_id: str):
        self.client = client
        self.run_id = run_id
        self.bronze = f"{CATALOG}.{SCHEMA_PREFIX}_bronze"
        self.silver = f"{CATALOG}.{SCHEMA_PREFIX}_silver"
        self.monitoring = f"{CATALOG}.{SCHEMA_PREFIX}_monitoring"
        self.volume_root = f"/Volumes/{CATALOG}/{SCHEMA_PREFIX}_bronze/{RAW_VOLUME}"

    def validate_prerequisites(self) -> None:
        """Require the existing foundation and create only the raw artifact volume."""
        validate_identifier(CATALOG, "catalog")
        for schema in (self.bronze, self.silver, self.monitoring):
            result = self.client.execute(f"SHOW TABLES IN {schema}")
            if not result.get("result", {}).get("data_array"):
                raise RuntimeError(f"Required existing schema has no tables: {schema}")
        self.client.execute(
            f"CREATE VOLUME IF NOT EXISTS {self.bronze}.{RAW_VOLUME} "
            "COMMENT 'Untouched official source artifacts for FuelSignal ingestion'"
        )

    def upload(self, name: str, content: bytes) -> str:
        """Upload an untouched or derived staging artifact to the UC Volume."""
        remote_path = f"{self.volume_root}/{self.run_id}/{name}"
        response = requests.put(
            f"{self.client.host}/api/2.0/fs/files{remote_path}",
            params={"overwrite": "true"},
            headers={
                "Authorization": f"Bearer {self.client.token}",
                "Content-Type": "application/octet-stream",
            },
            data=content,
            timeout=180,
        )
        if not response.ok:
            raise RuntimeError(
                f"Databricks artifact upload failed for {name}: "
                f"HTTP {response.status_code} {response.reason}"
            )
        return remote_path

    def merge_fuelcheck(self, path: str, source_url: str, source_file_name: str) -> None:
        """Merge FuelCheck price and station rows by deterministic source hashes."""
        run_id = sql_literal(self.run_id)
        url = sql_literal(source_url)
        file_name = sql_literal(source_file_name)
        source = f"read_files({sql_literal(path)}, format => 'json')"
        self.client.execute(
            f"""
            MERGE INTO {self.bronze}.bronze_fuelcheck_prices_raw AS target
            USING (
              SELECT
                sha2(concat_ws('||', ServiceStationName, Address, cast(Postcode AS STRING)), 256) station_code,
                cast(ServiceStationName AS STRING) station_name,
                cast(Brand AS STRING) brand,
                cast(Address AS STRING) address,
                cast(Suburb AS STRING) suburb,
                cast(Postcode AS STRING) postcode,
                cast(NULL AS DOUBLE) latitude,
                cast(NULL AS DOUBLE) longitude,
                cast(FuelCode AS STRING) fuel_type,
                try_cast(Price AS DOUBLE) price,
                cast(PriceUpdatedDate AS STRING) last_updated,
                to_json(named_struct(
                  'ServiceStationName', ServiceStationName, 'Address', Address,
                  'Suburb', Suburb, 'Postcode', Postcode, 'Brand', Brand,
                  'FuelCode', FuelCode, 'PriceUpdatedDate', PriceUpdatedDate, 'Price', Price
                )) raw_json,
                current_timestamp() _ingested_at,
                'nsw_fuelcheck' _source_name,
                {url} _source_url,
                {file_name} _source_file,
                sha2(concat_ws('||', ServiceStationName, Address, cast(Postcode AS STRING),
                  Brand, FuelCode, cast(PriceUpdatedDate AS STRING), cast(Price AS STRING)), 256) _source_record_hash,
                {run_id} _pipeline_run_id
              FROM {source}
            ) AS source
            ON target._source_record_hash = source._source_record_hash
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        self.client.execute(
            f"""
                        MERGE INTO {self.bronze}.bronze_fuelcheck_stations_raw AS target
                        USING (
                            SELECT DISTINCT
                                sha2(concat_ws('||', ServiceStationName, Address, cast(Postcode AS STRING)), 256) station_code,
                                cast(ServiceStationName AS STRING) station_name,
                                cast(Brand AS STRING) brand,
                                cast(Address AS STRING) address,
                                cast(Suburb AS STRING) suburb,
                                'NSW' state,
                                cast(Postcode AS STRING) postcode,
                                cast(NULL AS DOUBLE) latitude,
                                cast(NULL AS DOUBLE) longitude,
                                cast(NULL AS STRING) station_type,
                                to_json(named_struct('ServiceStationName', ServiceStationName, 'Address', Address,
                                    'Suburb', Suburb, 'Postcode', Postcode, 'Brand', Brand)) raw_json,
                                current_timestamp() _ingested_at,
                                'nsw_fuelcheck' _source_name,
                                {url} _source_url,
                                {file_name} _source_file,
                                sha2(concat_ws('||', ServiceStationName, Address, cast(Postcode AS STRING), Brand), 256) _source_record_hash,
                                {run_id} _pipeline_run_id
                            FROM {source}
                        ) AS source
                        ON target._source_record_hash = source._source_record_hash
                        WHEN NOT MATCHED THEN INSERT *
            """
        )

    def clean_current_fuelcheck_resource(self) -> None:
        """Remove prior parser artifacts for the current official FuelCheck file only."""
        current_resource = "_source_name = 'nsw_fuelcheck' AND _source_file IN ("
        current_resource += "'fuelcheck_march_2026.csv', "
        current_resource += "'fuelcheck_march_2026_staging.csv', "
        current_resource += "'fuelcheck_march_2026_staging.jsonl')"
        self.client.execute(
            f"""
            DELETE FROM {self.silver}.silver_data_quality_issues
            WHERE record_identifier IN (
              SELECT _source_record_hash
              FROM {self.bronze}.bronze_fuelcheck_prices_raw
              WHERE {current_resource}
            )
            """
        )
        self.client.execute(
            f"DELETE FROM {self.bronze}.bronze_fuelcheck_prices_raw WHERE {current_resource}"
        )
        self.client.execute(
            f"""
            DELETE FROM {self.bronze}.bronze_fuelcheck_stations_raw
            WHERE {current_resource}
            """
        )

    def merge_aip(self, path: str, source_url: str) -> None:
        """Merge parsed AIP workbook records while preserving the workbook artifact."""
        self.client.execute(
            f"""
            MERGE INTO {self.bronze}.bronze_aip_tgp_raw AS target
            USING (
              SELECT cast(tgp_date AS STRING) tgp_date, cast(terminal AS STRING) terminal,
                cast(city AS STRING) city, cast(product AS STRING) product,
                cast(price_cpl AS STRING) price_cpl, cast(raw_json AS STRING) raw_html_snippet,
                current_timestamp() _ingested_at, 'aip_terminal_gate_prices' _source_name,
                {sql_literal(source_url)} _source_url, {sql_literal(Path(path).name)} _source_file,
                sha2(concat_ws('||', tgp_date, terminal, product, price_cpl), 256) _source_record_hash,
                {sql_literal(self.run_id)} _pipeline_run_id
              FROM read_files({sql_literal(path)}, format => 'csv', header => true)
            ) AS source
            ON target._source_record_hash = source._source_record_hash
            WHEN NOT MATCHED THEN INSERT *
            """
        )

    def merge_holidays(self, path: str, source_url: str) -> None:
        """Merge NSW holiday page rows by deterministic source hashes."""
        self.client.execute(
            f"""
            MERGE INTO {self.bronze}.bronze_public_holidays_raw AS target
            USING (
              SELECT cast(date AS STRING) date, cast(holiday_name AS STRING) holiday_name,
                cast(state AS STRING) state, cast(is_national AS BOOLEAN) is_national,
                current_timestamp() _ingested_at, 'nsw_public_holidays' _source_name,
                {sql_literal(source_url)} _source_url, {sql_literal(Path(path).name)} _source_file,
                sha2(concat_ws('||', date, holiday_name, state), 256) _source_record_hash,
                {sql_literal(self.run_id)} _pipeline_run_id
              FROM read_files({sql_literal(path)}, format => 'csv', header => true)
            ) AS source
            ON target._source_record_hash = source._source_record_hash
            WHEN NOT MATCHED THEN INSERT *
            """
        )

    def write_quality_issues(self) -> None:
        """Persist FuelCheck, TGP, and holiday rule violations without deletion."""
        rules = [
            (
                "fuelcheck_coordinates_present",
                "bronze_fuelcheck_prices_raw",
                "silver_fuel_prices",
                "latitude IS NULL OR longitude IS NULL OR latitude NOT BETWEEN -37.5 AND -28.0 OR longitude NOT BETWEEN 141.0 AND 154.0",
                "latitude,longitude",
                "Missing or invalid NSW station coordinates",
            ),
            (
                "fuelcheck_price_bounds",
                "bronze_fuelcheck_prices_raw",
                "silver_fuel_prices",
                "price IS NULL OR price < 80 OR price > 300",
                "price",
                "Fuel price is outside configured bounds [80, 300] cpl",
            ),
            (
                "fuelcheck_timestamp_parseable",
                "bronze_fuelcheck_prices_raw",
                "silver_fuel_prices",
                "try_cast(last_updated AS TIMESTAMP) IS NULL",
                "last_updated",
                "Fuel price timestamp is not parseable",
            ),
            (
                "tgp_required_values",
                "bronze_aip_tgp_raw",
                "silver_terminal_gate_prices",
                "try_cast(tgp_date AS DATE) IS NULL OR try_cast(price_cpl AS DOUBLE) <= 0 OR terminal IS NULL OR product IS NULL",
                "tgp_date,terminal,product,price_cpl",
                "TGP date, terminal, product, or price is invalid",
            ),
            (
                "holiday_required_values",
                "bronze_public_holidays_raw",
                "silver_public_holidays",
                "try_cast(date AS DATE) IS NULL OR holiday_name IS NULL",
                "date,holiday_name",
                "Holiday date or name is invalid",
            ),
        ]
        for rule, source_table, target_table, predicate, columns, description in rules:
            identifier = "_source_record_hash"
            self.client.execute(
                f"""
                MERGE INTO {self.silver}.silver_data_quality_issues AS target
                USING (
                  SELECT sha2(concat_ws('||', {identifier}, {sql_literal(rule)}), 256) issue_id,
                    {sql_literal(self.run_id)} pipeline_run_id,
                    {sql_literal(self.bronze + '.' + source_table)} source_table,
                    {sql_literal(self.silver + '.' + target_table)} target_table,
                    {sql_literal(rule)} rule_name, 'error' severity,
                    {sql_literal(columns)} column_name, {identifier} record_identifier,
                    {sql_literal(description)} issue_description,
                    cast(NULL AS STRING) raw_value, 'quarantined' action_taken,
                    current_timestamp() detected_at
                  FROM {self.bronze}.{source_table}
                  WHERE {predicate}
                ) AS source
                ON target.issue_id = source.issue_id
                WHEN NOT MATCHED THEN INSERT *
                """
            )

    def transform_silver(self) -> None:
        """Merge valid first-stage canonical TGP and holiday records."""
        self.client.execute(
            f"""
                        MERGE INTO {self.silver}.silver_terminal_gate_prices AS target
                        USING (
                            SELECT try_cast(tgp_date AS DATE) tgp_date, trim(terminal) terminal,
                                trim(city) city,
                                CASE upper(trim(product)) WHEN 'ULP' THEN 'U91' WHEN 'DIESEL' THEN 'DL' ELSE upper(trim(product)) END fuel_type,
                                try_cast(price_cpl AS DOUBLE) tgp_cpl, _source_name source_name,
                                _ingested_at ingested_at, {sql_literal(self.run_id)} _pipeline_run_id
                            FROM {self.bronze}.bronze_aip_tgp_raw
                            WHERE try_cast(tgp_date AS DATE) IS NOT NULL AND try_cast(price_cpl AS DOUBLE) > 0
                                AND terminal IS NOT NULL AND product IS NOT NULL
                        ) AS source
                        ON target.tgp_date = source.tgp_date AND target.terminal = source.terminal
                            AND target.fuel_type = source.fuel_type
                        WHEN NOT MATCHED THEN INSERT *
                        """
        )
        self.client.execute(
            f"""
                        MERGE INTO {self.silver}.silver_public_holidays AS target
                        USING (
                            SELECT try_cast(date AS DATE) holiday_date, trim(holiday_name) holiday_name,
                                coalesce(nullif(trim(state), ''), 'NSW') state,
                                cast(is_national AS BOOLEAN) is_national,
                                year(try_cast(date AS DATE)) year,
                                dayofweek(try_cast(date AS DATE)) day_of_week,
                                _source_name source_name, {sql_literal(self.run_id)} _pipeline_run_id
                            FROM {self.bronze}.bronze_public_holidays_raw
                            WHERE try_cast(date AS DATE) IS NOT NULL AND holiday_name IS NOT NULL
                        ) AS source
                        ON target.holiday_date = source.holiday_date
                            AND target.holiday_name = source.holiday_name
                            AND target.state = source.state
                        WHEN NOT MATCHED THEN INSERT *
                        """
        )

    def update_quarantine_metrics(self) -> None:
        """Write current source rejection counts to monitoring_pipeline_runs."""
        rejected = self.count(
            f"(SELECT DISTINCT record_identifier FROM "
            f"{self.silver}.silver_data_quality_issues "
            f"WHERE source_table = '{self.bronze}.bronze_fuelcheck_prices_raw')"
        )
        self.client.execute(
            f"""
            UPDATE {self.monitoring}.monitoring_pipeline_runs
            SET records_quarantined = CASE
              WHEN pipeline_name = 'ingest_nsw_fuelcheck' THEN {rejected}
              ELSE 0
            END
            WHERE run_id LIKE {sql_literal(self.run_id + ':%')}
            """
        )

    def write_audit(
        self,
        source_name: str,
        source_url: str,
        started_at: datetime,
        duration: float,
        source_count: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """Upsert one source run into both required audit tables and freshness."""
        completed_at = datetime.now(timezone.utc)
        source_run_id = f"{self.run_id}:{source_name}"
        self.client.execute(
            f"""
            MERGE INTO {self.bronze}.bronze_ingestion_audit AS target
            USING (SELECT {sql_literal(source_run_id)} pipeline_run_id,
              {sql_literal(source_name)} source_name, {sql_literal(source_url)} source_url,
              try_cast({sql_literal(started_at.isoformat())} AS TIMESTAMP) ingestion_start_at,
              try_cast({sql_literal(completed_at.isoformat())} AS TIMESTAMP) ingestion_end_at,
              {duration} duration_seconds, {source_count} record_count,
              {sql_literal(status)} status, {sql_literal(error)} error_message,
              'dev' environment, current_timestamp() _ingested_at) AS source
            ON target.pipeline_run_id = source.pipeline_run_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        self.client.execute(
            f"""
            MERGE INTO {self.monitoring}.monitoring_pipeline_runs AS target
            USING (SELECT {sql_literal(source_run_id)} run_id,
              {sql_literal('ingest_' + source_name)} pipeline_name, 'bronze' stage,
              try_cast({sql_literal(started_at.isoformat())} AS TIMESTAMP) started_at,
              try_cast({sql_literal(completed_at.isoformat())} AS TIMESTAMP) completed_at,
              {duration} duration_seconds, {sql_literal(status)} status,
              {source_count} records_read, {source_count} records_written,
              cast(NULL AS BIGINT) records_quarantined, {sql_literal(error)} error_message,
              'dev' environment, {sql_literal(json.dumps({'source_url': source_url}))} parameters) source
            ON target.run_id = source.run_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        self.client.execute(
            f"""
            MERGE INTO {self.monitoring}.monitoring_source_freshness AS target
            USING (SELECT current_timestamp() check_timestamp,
              {sql_literal(source_name)} source_name, current_timestamp() last_ingestion_at,
              cast(0 AS DOUBLE) hours_since_last_ingestion, cast(48 AS DOUBLE) expected_max_hours,
              false is_stale, false alert_triggered, {source_count} record_count_last_run) source
            ON target.source_name = source.source_name
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )

    def count(self, table: str) -> int:
        """Return a table row count."""
        result = self.client.execute(f"SELECT count(*) FROM {table}")
        return int(result["result"]["data_array"][0][0])


def main() -> int:
    """Download all required sources and execute the live pipeline."""
    run_id = f"ingest-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    host, token = databricks_auth()
    sources = load_sources_config()["sources"]
    pipeline_config = load_project_config()["pipeline"]
    client = DatabricksSqlClient(
        host=host,
        token=token,
        warehouse_id=os.environ.get("DATABRICKS_WAREHOUSE_ID") or None,
    )
    pipeline = LivePipeline(client, run_id)

    try:
        pipeline.validate_prerequisites()
        downloaded: dict[str, dict[str, Any]] = {}
        for key in ("nsw_fuelcheck", "aip_terminal_gate_prices", "nsw_public_holidays"):
            source = sources[key]
            url = source["resolved_download_url"]
            started_at = datetime.now(timezone.utc)
            content, duration = fetch_required(
                url,
                timeout=pipeline_config["request_timeout_seconds"],
                retries=pipeline_config["max_retry_attempts"],
            )
            downloaded[key] = {
                "url": url,
                "content": content,
                "duration": duration,
                "started_at": started_at,
                "sha256": sha256_bytes(content),
            }

        fuel = downloaded["nsw_fuelcheck"]
        pipeline.upload("fuelcheck_march_2026.csv", fuel["content"])
        fuel_frame = pd.read_csv(io.BytesIO(fuel["content"]), dtype={"Postcode": str})
        fuel_count = len(fuel_frame)
        fuel_staging_path = pipeline.upload(
            "fuelcheck_march_2026_staging.jsonl", dataframe_jsonl_bytes(fuel_frame)
        )
        pipeline.clean_current_fuelcheck_resource()
        pipeline.merge_fuelcheck(fuel_staging_path, fuel["url"], "fuelcheck_march_2026.csv")
        pipeline.write_audit(
            "nsw_fuelcheck",
            fuel["url"],
            fuel["started_at"],
            fuel["duration"],
            fuel_count,
            "success",
        )

        aip = downloaded["aip_terminal_gate_prices"]
        aip_frame = parse_aip_workbook(aip["content"])
        pipeline.upload("aip_tgp_17_jul_2026.xlsx", aip["content"])
        aip_path = pipeline.upload("aip_tgp_staging.csv", dataframe_csv_bytes(aip_frame))
        pipeline.merge_aip(aip_path, aip["url"])
        pipeline.write_audit(
            "aip_terminal_gate_prices",
            aip["url"],
            aip["started_at"],
            aip["duration"],
            len(aip_frame),
            "success",
        )

        holidays = downloaded["nsw_public_holidays"]
        holiday_frame = parse_holiday_page(holidays["content"])
        pipeline.upload("nsw_public_holidays_2026_2027.html", holidays["content"])
        holiday_path = pipeline.upload(
            "nsw_public_holidays_staging.csv", dataframe_csv_bytes(holiday_frame)
        )
        pipeline.merge_holidays(holiday_path, holidays["url"])
        pipeline.write_audit(
            "nsw_public_holidays",
            holidays["url"],
            holidays["started_at"],
            holidays["duration"],
            len(holiday_frame),
            "success",
        )

        pipeline.write_quality_issues()
        pipeline.transform_silver()
        pipeline.update_quarantine_metrics()

        tables = [
            f"{pipeline.bronze}.bronze_fuelcheck_prices_raw",
            f"{pipeline.bronze}.bronze_fuelcheck_stations_raw",
            f"{pipeline.bronze}.bronze_aip_tgp_raw",
            f"{pipeline.bronze}.bronze_public_holidays_raw",
            f"{pipeline.silver}.silver_fuel_prices",
            f"{pipeline.silver}.silver_station_master",
            f"{pipeline.silver}.silver_terminal_gate_prices",
            f"{pipeline.silver}.silver_public_holidays",
            f"{pipeline.silver}.silver_data_quality_issues",
            f"{pipeline.bronze}.bronze_ingestion_audit",
            f"{pipeline.monitoring}.monitoring_pipeline_runs",
            f"{pipeline.monitoring}.monitoring_source_freshness",
        ]
        summary = {
            "run_id": run_id,
            "sources": {
                key: {"url": value["url"], "sha256": value["sha256"]}
                for key, value in downloaded.items()
            },
            "row_counts": {table: pipeline.count(table) for table in tables},
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Live ingestion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
