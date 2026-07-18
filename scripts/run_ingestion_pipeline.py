"""Run the live, idempotent FuelSignal Bronze and Silver pipeline."""

# ruff: noqa: E501, S603, S607, S608

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


def _find_project_root() -> Path:
    """Walk up from the current directory looking for pyproject.toml - robust to
    whatever directory Databricks' git_source spark_python_task execution happens
    to set as cwd (live-verified 2026-07-18: it's the script's own containing
    directory, e.g. .../scripts, not the repo root - Path.cwd() alone is wrong)."""
    candidate = Path.cwd()
    for _ in range(5):
        if (candidate / "pyproject.toml").exists():
            return candidate
        candidate = candidate.parent
    return Path.cwd()


try:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
except NameError:
    # __file__ is undefined under Databricks git_source exec-style execution.
    PROJECT_ROOT = _find_project_root()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from deploy_databricks_foundation import (  # noqa: E402
    DatabricksSqlClient,
    DeploymentError,
    validate_identifier,
)

from fuelsignal.config import (  # noqa: E402
    load_env,
    load_project_config,
    load_sources_config,
)
from fuelsignal.ingestion.fuelcheck_stations import (  # noqa: E402
    FuelCheckAuthError,
    FuelCheckStationReferenceIngester,
)

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"
RAW_VOLUME = "raw_sources"
USER_AGENT = "FuelSignal-Portfolio/0.2 (+https://github.com/ozzy2438/fuelsignal-retail-fuel-pricing-platform)"
DEFAULT_HOST = "https://dbc-aaefb4e4-e074.cloud.databricks.com"
FUELCHECK_MONTH_COLUMNS = [
    "ServiceStationName",
    "Address",
    "Suburb",
    "Postcode",
    "Brand",
    "FuelCode",
    "PriceUpdatedDate",
    "Price",
]


def sql_literal(value: str | None) -> str:
    """Return a safely quoted SQL string literal."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def sha256_bytes(content: bytes) -> str:
    """Return a deterministic SHA-256 artifact hash."""
    return hashlib.sha256(content).hexdigest()


def normalize_key_sql(address_expr: str, postcode_expr: str) -> str:
    """SQL equivalent of fuelsignal.silver.station_matching.normalize_address_key()."""

    def norm(expr: str) -> str:
        return (
            f"trim(regexp_replace(regexp_replace(upper(coalesce({expr}, '')), "
            "'[^A-Z0-9]', ' '), ' +', ' '))"
        )

    return f"concat({norm(address_expr)}, '|', {norm(postcode_expr)})"


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


JOB_SECRET_SCOPE = "fuelsignal"  # noqa: S105 - a secret *scope name*, not a secret
JOB_SECRET_KEY = "token"  # noqa: S105 - a secret *key name*, not a secret


def _cli_credential(flag: str) -> str | None:
    """Read a `--flag value` pair from sys.argv - Databricks Jobs' serverless
    spark_python_task does not reliably inject `spark_env_vars` into the process
    environment (live-verified 2026-07-18: DATABRICKS_HOST/TOKEN were empty inside
    the task despite being set on the task definition). Local/CLI execution never
    passes these flags, so this is a no-op there."""
    argv = sys.argv[1:]
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _dbutils_secret(scope: str, key: str) -> str | None:
    """Retrieve a secret via the Databricks Runtime's dbutils - the documented,
    reliable way to read a secret from inside a running job (works for both
    classic-cluster and serverless compute), used as the fallback after
    `{{secrets/scope/key}}` job-parameter templating was live-verified
    (2026-07-18) to NOT resolve for `spark_python_task.parameters` on this
    workspace (the literal, unsubstituted template string was received, causing
    an HTTP 401 "Credential was not sent"). Returns None outside a Databricks
    runtime (e.g. local execution) rather than raising."""
    try:
        from databricks.sdk.runtime import dbutils
    except ImportError:
        return None
    try:
        return dbutils.secrets.get(scope=scope, key=key)
    except Exception:
        return None


def databricks_auth() -> tuple[str, str]:
    """Load PAT environment variables, CLI-provided credentials (see
    `_cli_credential`), a Databricks Runtime secret (see `_dbutils_secret`), or a
    short-lived Databricks CLI OAuth token, in that order."""
    host = os.environ.get("DATABRICKS_HOST", "").strip() or _cli_credential("--databricks-host")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip() or _cli_credential("--databricks-token")
    if token and token.startswith("{{"):
        # An unsubstituted {{secrets/...}} template string, not a real credential.
        token = None
    if host and token:
        return host.rstrip("/"), token

    if host:
        dbutils_token = _dbutils_secret(JOB_SECRET_SCOPE, JOB_SECRET_KEY)
        if dbutils_token:
            return host.rstrip("/"), dbutils_token

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


def git_commit_short() -> str:
    """Return the short git commit hash for audit code_version tracking."""
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def parse_fuelcheck_month(content: bytes, filename: str) -> pd.DataFrame:
    """Parse one official FuelCheck monthly resource (CSV or XLSX) into a canonical frame."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(io.BytesIO(content), dtype={"Postcode": str})
    elif suffix in (".xlsx", ".xls"):
        frame = pd.read_excel(io.BytesIO(content), sheet_name=0, dtype={"Postcode": str})
    else:
        raise RuntimeError(f"Unsupported FuelCheck resource format for {filename}: {suffix}")

    missing = set(FUELCHECK_MONTH_COLUMNS) - set(frame.columns)
    if missing:
        raise RuntimeError(f"{filename} is missing expected columns: {sorted(missing)}")

    frame = frame[FUELCHECK_MONTH_COLUMNS].copy()
    # XLSX resources parse PriceUpdatedDate as a real datetime64 column, which pandas'
    # to_json serializes as epoch milliseconds rather than an ISO string - normalize to
    # a plain ISO string uniformly so CSV and XLSX months stage identically.
    frame["PriceUpdatedDate"] = pd.to_datetime(
        frame["PriceUpdatedDate"], errors="coerce"
    ).dt.strftime("%Y-%m-%dT%H:%M:%S")
    return frame


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
        self.code_version = git_commit_short()

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

    def ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """Idempotently add columns this phase introduced. Never drops or recreates tables."""
        result = self.client.execute(f"DESCRIBE TABLE {table}")
        existing = {
            row[0]
            for row in result.get("result", {}).get("data_array", [])
            if row and row[0] and not str(row[0]).startswith("#")
        }
        missing = {name: dtype for name, dtype in columns.items() if name not in existing}
        if not missing:
            return
        additions = ", ".join(f"{name} {dtype}" for name, dtype in missing.items())
        self.client.execute(f"ALTER TABLE {table} ADD COLUMNS ({additions})")

    def run_migrations(self) -> None:
        """Add the columns this phase requires to already-deployed tables."""
        self.ensure_columns(
            f"{self.bronze}.bronze_ingestion_audit",
            {
                "stage": "STRING",
                "source_file": "STRING",
                "source_checksum": "STRING",
                "records_read": "LONG",
                "records_written": "LONG",
                "records_rejected": "LONG",
                "source_date_range": "STRING",
                "code_version": "STRING",
            },
        )
        self.ensure_columns(
            f"{self.silver}.silver_station_master",
            {
                "official_station_code": "STRING",
                "match_method": "STRING",
                "match_confidence": "DOUBLE",
                "effective_from": "DATE",
                "effective_to": "DATE",
                "ingested_at": "TIMESTAMP",
            },
        )
        self.ensure_columns(
            f"{self.silver}.silver_competitor_pairs",
            {"created_at": "TIMESTAMP"},
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

    def last_checksum(self, source_name: str, source_file: str) -> str | None:
        """Return the checksum of the last successful ingest of a specific source file."""
        result = self.client.execute(
            f"""
            SELECT source_checksum FROM {self.bronze}.bronze_ingestion_audit
            WHERE source_name = {sql_literal(source_name)}
              AND source_file = {sql_literal(source_file)}
              AND status = 'success'
            ORDER BY ingestion_end_at DESC
            LIMIT 1
            """
        )
        rows = result.get("result", {}).get("data_array", [])
        return rows[0][0] if rows and rows[0] else None

    def merge_fuelcheck(self, path: str, source_url: str, source_file_name: str) -> None:
        """Merge one month of FuelCheck price and station rows by deterministic source hashes."""
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

    def merge_station_reference(self, path: str) -> None:
        """Merge official FuelCheck station reference rows (with coordinates) by content hash."""
        source = f"read_files({sql_literal(path)}, format => 'json')"
        self.client.execute(
            f"""
            MERGE INTO {self.bronze}.bronze_fuelcheck_stations_raw AS target
            USING (
              SELECT
                cast(station_code AS STRING) station_code,
                cast(station_name AS STRING) station_name,
                cast(brand AS STRING) brand,
                cast(address AS STRING) address,
                cast(suburb AS STRING) suburb,
                cast(state AS STRING) state,
                cast(postcode AS STRING) postcode,
                cast(latitude AS DOUBLE) latitude,
                cast(longitude AS DOUBLE) longitude,
                cast(station_type AS STRING) station_type,
                cast(raw_json AS STRING) raw_json,
                try_cast(_ingested_at AS TIMESTAMP) _ingested_at,
                cast(_source_name AS STRING) _source_name,
                cast(_source_url AS STRING) _source_url,
                cast(_source_file AS STRING) _source_file,
                cast(_source_record_hash AS STRING) _source_record_hash,
                cast(_pipeline_run_id AS STRING) _pipeline_run_id
              FROM {source}
            ) AS source
            ON target._source_record_hash = source._source_record_hash
            WHEN NOT MATCHED THEN INSERT *
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
        """Persist TGP and holiday rule violations without deletion."""
        rules = [
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

    def write_unmatched_station_issues(self) -> None:
        """Flag bronze price rows whose station could not be resolved to a coordinate.

        This is the actionable reason a otherwise-valid price row does not reach
        silver_fuel_prices: the FuelCheck bulk archive never carries coordinates
        directly, so every row depends on a normalized address+postcode match
        against silver_station_master (populated from the live reference API).
        """
        match_bronze = normalize_key_sql("p.address", "p.postcode")
        match_silver = normalize_key_sql("sm.address", "sm.postcode")
        self.client.execute(
            f"""
            MERGE INTO {self.silver}.silver_data_quality_issues AS target
            USING (
              SELECT sha2(concat_ws('||', p._source_record_hash, 'fuelcheck_station_unmatched'), 256) issue_id,
                {sql_literal(self.run_id)} pipeline_run_id,
                {sql_literal(self.bronze + '.bronze_fuelcheck_prices_raw')} source_table,
                {sql_literal(self.silver + '.silver_fuel_prices')} target_table,
                'fuelcheck_station_unmatched' rule_name, 'error' severity,
                'address,postcode' column_name, p._source_record_hash record_identifier,
                'Station address/postcode did not match any coordinate-bearing station_master row' issue_description,
                p.address raw_value, 'quarantined' action_taken, current_timestamp() detected_at
              FROM {self.bronze}.bronze_fuelcheck_prices_raw p
              LEFT JOIN {self.silver}.silver_station_master sm
                ON {match_bronze} = {match_silver}
              WHERE sm.station_id IS NULL
                AND try_cast(p.last_updated AS TIMESTAMP) IS NOT NULL
                AND p.price BETWEEN 80 AND 300
            ) AS source
            ON target.issue_id = source.issue_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )

    def transform_silver(self) -> None:
        """Merge valid canonical TGP and holiday records."""
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

    def build_station_master(self) -> None:
        """Crosswalk official (coordinate-bearing) stations against the bulk price archive.

        The bulk archive never carries an official station code, so the only viable
        deterministic join key is normalized address+postcode. Matches are only
        inserted when both sides resolve to exactly one station for that key;
        anything ambiguous or unresolved is reported via silver_data_quality_issues
        instead of being guessed.
        """
        run_id = sql_literal(self.run_id)
        match_key_expr = normalize_key_sql("address", "postcode")
        # Shared CTEs: match_key is computed once per side (bare column references,
        # since these subqueries define their own row context) and then only ever
        # referenced via the already-materialized alias.match_key downstream - never
        # recomputed against an alias that doesn't exist yet in that scope.
        common_ctes = f"""
            WITH official AS (
              SELECT station_code, station_name, brand, address, suburb, postcode, state,
                latitude, longitude, {match_key_expr} match_key,
                row_number() OVER (PARTITION BY station_code ORDER BY _ingested_at DESC) rn
              FROM {self.bronze}.bronze_fuelcheck_stations_raw
              WHERE _source_name = 'nsw_fuelcheck_api_reference'
            ),
            official_dedup AS (
              SELECT * FROM official WHERE rn = 1
            ),
            official_counts AS (
              SELECT match_key, count(DISTINCT station_code) n FROM official_dedup GROUP BY match_key
            ),
            bulk AS (
              SELECT DISTINCT station_code, address, postcode, {match_key_expr} match_key
              FROM {self.bronze}.bronze_fuelcheck_stations_raw
              WHERE _source_name = 'nsw_fuelcheck'
            ),
            bulk_counts AS (
              SELECT match_key, count(DISTINCT station_code) n FROM bulk GROUP BY match_key
            )
        """

        # Step 1: confident 1:1 matches (official station <-> bulk-archive station).
        self.client.execute(
            f"""
            {common_ctes}
            MERGE INTO {self.silver}.silver_station_master AS target
            USING (
              SELECT
                sha2(o.station_code, 256) station_id,
                o.station_code station_code, o.station_name station_name, o.brand brand,
                upper(o.brand) brand_normalized, o.address address, o.suburb suburb,
                o.postcode postcode, o.state state, o.latitude latitude, o.longitude longitude,
                true is_active, current_date() first_seen_date, current_date() last_seen_date,
                'nsw_fuelcheck_api_reference' source_name, {run_id} _pipeline_run_id,
                o.station_code official_station_code, 'exact_address_postcode' match_method,
                cast(1.0 AS DOUBLE) match_confidence, current_date() effective_from,
                cast(NULL AS DATE) effective_to, current_timestamp() ingested_at
              FROM official_dedup o
              JOIN bulk b ON o.match_key = b.match_key
              JOIN official_counts oc ON oc.match_key = o.match_key
              JOIN bulk_counts bc ON bc.match_key = b.match_key
              WHERE oc.n = 1 AND bc.n = 1
            ) AS source
            ON target.station_id = source.station_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )

        # Step 2: official stations with no bulk-archive counterpart yet - still
        # legitimate, coordinate-bearing, officially-sourced stations.
        self.client.execute(
            f"""
            {common_ctes}
            MERGE INTO {self.silver}.silver_station_master AS target
            USING (
              SELECT
                sha2(o.station_code, 256) station_id,
                o.station_code station_code, o.station_name station_name, o.brand brand,
                upper(o.brand) brand_normalized, o.address address, o.suburb suburb,
                o.postcode postcode, o.state state, o.latitude latitude, o.longitude longitude,
                true is_active, current_date() first_seen_date, current_date() last_seen_date,
                'nsw_fuelcheck_api_reference' source_name, {run_id} _pipeline_run_id,
                o.station_code official_station_code, 'reference_only_no_bulk_match' match_method,
                cast(1.0 AS DOUBLE) match_confidence, current_date() effective_from,
                cast(NULL AS DATE) effective_to, current_timestamp() ingested_at
              FROM official_dedup o
              LEFT JOIN bulk b ON o.match_key = b.match_key
              WHERE b.match_key IS NULL
            ) AS source
            ON target.station_id = source.station_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )

        # Step 3: ambiguous matches (a normalized key maps to >1 distinct station on
        # either side) - quarantined, never guessed.
        self.client.execute(
            f"""
            {common_ctes}
            MERGE INTO {self.silver}.silver_data_quality_issues AS target
            USING (
              SELECT sha2(concat_ws('||', o.station_code, 'station_match_ambiguous'), 256) issue_id,
                {run_id} pipeline_run_id,
                {sql_literal(self.bronze + ".bronze_fuelcheck_stations_raw")} source_table,
                {sql_literal(self.silver + ".silver_station_master")} target_table,
                'station_match_ambiguous' rule_name, 'warning' severity,
                'address,postcode' column_name, o.station_code record_identifier,
                'Normalized address+postcode key matches more than one station on the official or bulk side' issue_description,
                o.address raw_value, 'quarantined' action_taken, current_timestamp() detected_at
              FROM official_dedup o
              JOIN bulk b ON o.match_key = b.match_key
              JOIN official_counts oc ON oc.match_key = o.match_key
              JOIN bulk_counts bc ON bc.match_key = b.match_key
              WHERE oc.n > 1 OR bc.n > 1
            ) AS source
            ON target.issue_id = source.issue_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )

        # Step 4: bulk-archive stations with no official counterpart at all - no
        # coordinates are available for these; report, never fabricate.
        self.client.execute(
            f"""
            {common_ctes}
            MERGE INTO {self.silver}.silver_data_quality_issues AS target
            USING (
              SELECT sha2(concat_ws('||', b.station_code, 'station_unmatched_no_coordinates'), 256) issue_id,
                {run_id} pipeline_run_id,
                {sql_literal(self.bronze + ".bronze_fuelcheck_stations_raw")} source_table,
                {sql_literal(self.silver + ".silver_station_master")} target_table,
                'station_unmatched_no_coordinates' rule_name, 'error' severity,
                'address,postcode' column_name, b.station_code record_identifier,
                'No official FuelCheck reference station shares this normalized address+postcode - no coordinates available' issue_description,
                b.address raw_value, 'quarantined' action_taken, current_timestamp() detected_at
              FROM bulk b
              LEFT JOIN official_dedup o ON o.match_key = b.match_key
              WHERE o.match_key IS NULL
            ) AS source
            ON target.issue_id = source.issue_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )

    def transform_fuel_prices_silver(self) -> None:
        """Populate silver_fuel_prices for price rows resolved to a coordinate-bearing station."""
        match_bronze = normalize_key_sql("p.address", "p.postcode")
        match_silver = normalize_key_sql("sm.address", "sm.postcode")
        self.client.execute(
            f"""
            MERGE INTO {self.silver}.silver_fuel_prices AS target
            USING (
              SELECT sm.station_id station_id, p.station_name station_name, p.brand brand,
                p.address address, p.suburb suburb, p.postcode postcode,
                sm.latitude latitude, sm.longitude longitude,
                upper(trim(p.fuel_type)) fuel_type,
                try_cast(p.last_updated AS TIMESTAMP) observed_at,
                date(try_cast(p.last_updated AS TIMESTAMP)) observed_date,
                p.price price_cpl, p._source_name source_name, p._ingested_at ingested_at,
                {sql_literal(self.run_id)} _pipeline_run_id
              FROM {self.bronze}.bronze_fuelcheck_prices_raw p
              JOIN {self.silver}.silver_station_master sm ON {match_bronze} = {match_silver}
              WHERE try_cast(p.last_updated AS TIMESTAMP) IS NOT NULL
                AND p.price BETWEEN 80 AND 300
            ) AS source
            ON target.station_id = source.station_id
              AND target.fuel_type = source.fuel_type
              AND target.observed_at = source.observed_at
            WHEN NOT MATCHED THEN INSERT *
            """
        )

    def build_competitor_pairs(self) -> None:
        """Compute 5km competitor pairs via Haversine with a bounding-box pre-filter.

        Pairs are stored in a single direction (station_id < competitor_station_id)
        since the relationship is symmetric; consumers needing a per-station lookup
        should query both columns.
        """
        radius_km = 5.0
        # ~0.06 deg latitude and ~0.07 deg longitude both exceed 5km at NSW latitudes,
        # giving a safe pre-filter before the exact Haversine calculation below.
        self.client.execute(
            f"""
            MERGE INTO {self.silver}.silver_competitor_pairs AS target
            USING (
              SELECT station_id, competitor_station_id, distance_km FROM (
                SELECT a.station_id station_id, b.station_id competitor_station_id,
                  6371 * acos(least(1.0, greatest(-1.0,
                    cos(radians(a.latitude)) * cos(radians(b.latitude)) * cos(radians(b.longitude) - radians(a.longitude))
                    + sin(radians(a.latitude)) * sin(radians(b.latitude))
                  ))) distance_km
                FROM {self.silver}.silver_station_master a
                JOIN {self.silver}.silver_station_master b
                  ON a.station_id < b.station_id
                  AND b.latitude BETWEEN a.latitude - 0.06 AND a.latitude + 0.06
                  AND b.longitude BETWEEN a.longitude - 0.07 AND a.longitude + 0.07
                WHERE a.is_active AND b.is_active
              )
              WHERE distance_km <= {radius_km}
            ) AS source
            ON target.station_id = source.station_id AND target.competitor_station_id = source.competitor_station_id
            WHEN MATCHED THEN UPDATE SET distance_km = source.distance_km
            WHEN NOT MATCHED THEN INSERT (station_id, competitor_station_id, distance_km,
              effective_from, effective_to, calculation_method, _pipeline_run_id, created_at)
              VALUES (source.station_id, source.competitor_station_id, source.distance_km,
                current_date(), NULL, 'haversine_bbox_prefilter_single_direction',
                {sql_literal(self.run_id)}, current_timestamp())
            """
        )

    def write_monitoring_dq_results(self) -> None:
        """Summarize this run's data-quality outcomes into monitoring_data_quality_results."""
        self.client.execute(
            f"""
            INSERT INTO {self.monitoring}.monitoring_data_quality_results
            SELECT {sql_literal(self.run_id)} run_id, current_timestamp() check_timestamp,
              target_table table_name, rule_name, issue_description rule_description,
              severity, cast(NULL AS LONG) total_records, cast(NULL AS LONG) passed_records,
              count(*) failed_records, cast(NULL AS DOUBLE) pass_rate,
              cast(NULL AS DOUBLE) threshold,
              CASE WHEN severity = 'error' THEN 'fail' ELSE 'warn' END status,
              to_json(named_struct('source_table', source_table)) details
            FROM {self.silver}.silver_data_quality_issues
            WHERE pipeline_run_id = {sql_literal(self.run_id)}
            GROUP BY target_table, rule_name, issue_description, severity, source_table
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
        stage: str = "bronze",
        source_file: str = "default",
        source_checksum: str | None = None,
        records_read: int | None = None,
        records_written: int | None = None,
        records_rejected: int | None = None,
        source_date_range: str | None = None,
    ) -> None:
        """Upsert one source-file run into audit, monitoring, and freshness tables."""
        completed_at = datetime.now(timezone.utc)
        source_run_id = f"{self.run_id}:{source_name}:{source_file}"
        self.client.execute(
            f"""
            MERGE INTO {self.bronze}.bronze_ingestion_audit AS target
            USING (SELECT {sql_literal(source_run_id)} pipeline_run_id,
              {sql_literal(source_name)} source_name, {sql_literal(source_url)} source_url,
              try_cast({sql_literal(started_at.isoformat())} AS TIMESTAMP) ingestion_start_at,
              try_cast({sql_literal(completed_at.isoformat())} AS TIMESTAMP) ingestion_end_at,
              {duration} duration_seconds, {source_count} record_count,
              {sql_literal(status)} status, {sql_literal(error)} error_message,
              'dev' environment, current_timestamp() _ingested_at,
              {sql_literal(stage)} stage, {sql_literal(source_file)} source_file,
              {sql_literal(source_checksum)} source_checksum,
              {"NULL" if records_read is None else records_read} records_read,
              {"NULL" if records_written is None else records_written} records_written,
              {"NULL" if records_rejected is None else records_rejected} records_rejected,
              {sql_literal(source_date_range)} source_date_range,
              {sql_literal(self.code_version)} code_version) AS source
            ON target.pipeline_run_id = source.pipeline_run_id
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        self.client.execute(
            f"""
            MERGE INTO {self.monitoring}.monitoring_pipeline_runs AS target
            USING (SELECT {sql_literal(source_run_id)} run_id,
              {sql_literal('ingest_' + source_name)} pipeline_name, {sql_literal(stage)} stage,
              try_cast({sql_literal(started_at.isoformat())} AS TIMESTAMP) started_at,
              try_cast({sql_literal(completed_at.isoformat())} AS TIMESTAMP) completed_at,
              {duration} duration_seconds, {sql_literal(status)} status,
              {source_count} records_read, {source_count} records_written,
              cast(NULL AS BIGINT) records_quarantined, {sql_literal(error)} error_message,
              'dev' environment, {sql_literal(json.dumps({"source_url": source_url}))} parameters) source
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


def ingest_fuelcheck_history(
    pipeline: LivePipeline,
    historical_resources: list[dict[str, str]],
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    """Download and merge each configured historical month, skipping unchanged files."""
    summary = {
        "months_processed": 0,
        "months_skipped_unchanged": 0,
        "months_failed": [],
        "total_rows": 0,
    }
    for resource in historical_resources:
        month = resource["month"]
        url = resource["url"]
        filename = Path(url).name
        started_at = datetime.now(timezone.utc)
        try:
            content, duration = fetch_required(url, timeout=timeout, retries=retries)
            checksum = sha256_bytes(content)
            previous_checksum = pipeline.last_checksum("nsw_fuelcheck", filename)
            if previous_checksum == checksum:
                summary["months_skipped_unchanged"] += 1
                print(f"  {month} ({filename}): unchanged, skipped", file=sys.stderr)
                continue

            frame = parse_fuelcheck_month(content, filename)
            row_count = len(frame)
            staging_path = pipeline.upload(
                f"fuelcheck_{month}_staging.jsonl", dataframe_jsonl_bytes(frame)
            )
            pipeline.merge_fuelcheck(staging_path, url, filename)
            pipeline.write_audit(
                "nsw_fuelcheck",
                url,
                started_at,
                duration,
                row_count,
                "success",
                stage="bronze",
                source_file=filename,
                source_checksum=checksum,
                records_read=row_count,
                records_written=row_count,
                records_rejected=0,
                source_date_range=month,
            )
            summary["months_processed"] += 1
            summary["total_rows"] += row_count
            print(f"  {month} ({filename}): merged {row_count} rows", file=sys.stderr)
        except (requests.RequestException, RuntimeError) as exc:
            pipeline.write_audit(
                "nsw_fuelcheck",
                url,
                started_at,
                (datetime.now(timezone.utc) - started_at).total_seconds(),
                0,
                "failed",
                error=str(exc)[:500],
                stage="bronze",
                source_file=filename,
                source_date_range=month,
            )
            summary["months_failed"].append({"month": month, "error": str(exc)[:200]})
            print(f"  {month} ({filename}): FAILED - {exc}", file=sys.stderr)
    return summary


def ingest_station_reference(pipeline: LivePipeline) -> dict[str, Any]:
    """Fetch and merge the live official station reference dataset."""
    started_at = datetime.now(timezone.utc)
    ingester = FuelCheckStationReferenceIngester()
    try:
        payload = ingester.fetch()
        records = ingester.to_raw_records(payload)
        frame = pd.DataFrame(records)
        staging_path = pipeline.upload(
            "fuelcheck_referencedata_lovs_staging.jsonl", dataframe_jsonl_bytes(frame)
        )
        pipeline.merge_station_reference(staging_path)
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        pipeline.write_audit(
            "nsw_fuelcheck_api_reference",
            ingester.metadata["data_url"],
            started_at,
            duration,
            len(records),
            "success",
            stage="bronze",
            source_file="fuelcheck_referencedata_lovs",
            records_read=len(records),
            records_written=len(records),
            records_rejected=0,
        )
        return {"status": "success", "record_count": len(records)}
    except (FuelCheckAuthError, requests.RequestException, RuntimeError) as exc:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        pipeline.write_audit(
            "nsw_fuelcheck_api_reference",
            "https://api.onegov.nsw.gov.au/FuelCheckRefData/v2/fuel/lovs",
            started_at,
            duration,
            0,
            "failed",
            error=str(exc)[:500],
            stage="bronze",
            source_file="fuelcheck_referencedata_lovs",
        )
        return {"status": "failed", "error": str(exc)[:300]}


def main() -> int:
    """Download all required sources and execute the live pipeline."""
    load_env()
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
        pipeline.run_migrations()

        print("Fetching official station reference data...", file=sys.stderr)
        station_ref_summary = ingest_station_reference(pipeline)

        print("Ingesting FuelCheck historical price archive...", file=sys.stderr)
        fuelcheck_summary = ingest_fuelcheck_history(
            pipeline,
            sources["nsw_fuelcheck"]["historical_resources"],
            timeout=pipeline_config["request_timeout_seconds"],
            retries=pipeline_config["max_retry_attempts"],
        )

        aip_source = sources["aip_terminal_gate_prices"]
        aip_url = aip_source["resolved_download_url"]
        aip_started_at = datetime.now(timezone.utc)
        aip_content, aip_duration = fetch_required(
            aip_url,
            timeout=pipeline_config["request_timeout_seconds"],
            retries=pipeline_config["max_retry_attempts"],
        )
        aip_frame = parse_aip_workbook(aip_content)
        pipeline.upload(Path(aip_url).name, aip_content)
        aip_path = pipeline.upload("aip_tgp_staging.csv", dataframe_csv_bytes(aip_frame))
        pipeline.merge_aip(aip_path, aip_url)
        pipeline.write_audit(
            "aip_terminal_gate_prices",
            aip_url,
            aip_started_at,
            aip_duration,
            len(aip_frame),
            "success",
            stage="bronze",
            source_file=Path(aip_url).name,
            source_checksum=sha256_bytes(aip_content),
            records_read=len(aip_frame),
            records_written=len(aip_frame),
            records_rejected=0,
        )

        holidays_source = sources["nsw_public_holidays"]
        holidays_url = holidays_source["resolved_download_url"]
        holidays_started_at = datetime.now(timezone.utc)
        holidays_content, holidays_duration = fetch_required(
            holidays_url,
            timeout=pipeline_config["request_timeout_seconds"],
            retries=pipeline_config["max_retry_attempts"],
        )
        holiday_frame = parse_holiday_page(holidays_content)
        pipeline.upload("nsw_public_holidays_2026_2027.html", holidays_content)
        holiday_path = pipeline.upload(
            "nsw_public_holidays_staging.csv", dataframe_csv_bytes(holiday_frame)
        )
        pipeline.merge_holidays(holiday_path, holidays_url)
        pipeline.write_audit(
            "nsw_public_holidays",
            holidays_url,
            holidays_started_at,
            holidays_duration,
            len(holiday_frame),
            "success",
            stage="bronze",
            source_file="nsw_public_holidays_2026_2027.html",
            source_checksum=sha256_bytes(holidays_content),
            records_read=len(holiday_frame),
            records_written=len(holiday_frame),
            records_rejected=0,
        )

        print("Building silver_station_master crosswalk...", file=sys.stderr)
        pipeline.build_station_master()

        print("Transforming silver_fuel_prices...", file=sys.stderr)
        pipeline.transform_fuel_prices_silver()

        print("Building 5km competitor pairs...", file=sys.stderr)
        pipeline.build_competitor_pairs()

        pipeline.write_quality_issues()
        pipeline.write_unmatched_station_issues()
        pipeline.transform_silver()
        pipeline.write_monitoring_dq_results()
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
            f"{pipeline.silver}.silver_competitor_pairs",
            f"{pipeline.silver}.silver_data_quality_issues",
            f"{pipeline.bronze}.bronze_ingestion_audit",
            f"{pipeline.monitoring}.monitoring_pipeline_runs",
            f"{pipeline.monitoring}.monitoring_source_freshness",
            f"{pipeline.monitoring}.monitoring_data_quality_results",
        ]
        summary = {
            "run_id": run_id,
            "station_reference": station_ref_summary,
            "fuelcheck_history": fuelcheck_summary,
            "row_counts": {table: pipeline.count(table) for table in tables},
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Live ingestion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code != 0:
        # Databricks' git_source spark_python_task execution (an exec-style,
        # non-notebook context) treats *any* raised SystemExit - even SystemExit(0)
        # - as a task failure (live-verified 2026-07-18: a script that printed a
        # full success summary and returned 0 was still marked FAILED). Only raise
        # on a genuine non-zero exit code.
        raise SystemExit(_exit_code)
