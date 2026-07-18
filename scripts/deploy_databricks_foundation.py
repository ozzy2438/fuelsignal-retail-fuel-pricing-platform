"""Deploy the FuelSignal catalog, schemas, and Delta tables to Databricks."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fuelsignal.bronze.schemas import get_bronze_ddl  # noqa: E402
from fuelsignal.config import get_databricks_config  # noqa: E402
from fuelsignal.gold.schemas import get_gold_ddl  # noqa: E402
from fuelsignal.monitoring import get_monitoring_ddl  # noqa: E402
from fuelsignal.silver.schemas import get_silver_ddl  # noqa: E402

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}


class DeploymentError(RuntimeError):
    """Raised when the Databricks foundation cannot be deployed safely."""


def validate_identifier(value: str, label: str) -> str:
    """Reject unsafe SQL identifiers before interpolating them into DDL."""
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {label}: use only letters, numbers, and underscores")
    return value


def quote_identifier(value: str) -> str:
    """Quote a previously validated Databricks SQL identifier."""
    return f"`{validate_identifier(value, 'identifier')}`"


@dataclass
class DatabricksSqlClient:
    """Minimal Databricks Statement Execution API client."""

    host: str
    token: str
    warehouse_id: str | None = None
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        self.host = self.host.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "fuelsignal-foundation-deployer/0.1",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._session.request(
                method,
                f"{self.host}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise DeploymentError(f"Databricks request failed: {exc}") from exc

        if not response.ok:
            try:
                payload = response.json()
                message = payload.get("message") or payload.get("error_code") or response.reason
            except ValueError:
                message = response.reason
            raise DeploymentError(f"Databricks API returned HTTP {response.status_code}: {message}")

        if not response.content:
            return {}
        return response.json()

    def ensure_warehouse(self) -> str:
        """Use an explicit warehouse or select an available SQL warehouse."""
        if self.warehouse_id:
            return self.warehouse_id

        payload = self._request("GET", "/api/2.0/sql/warehouses")
        warehouses = payload.get("warehouses", [])
        if not warehouses:
            raise DeploymentError(
                "No SQL warehouse is available. Create or start a Databricks SQL warehouse first."
            )

        warehouses.sort(
            key=lambda item: (
                item.get("state") != "RUNNING",
                item.get("warehouse_type") != "PRO",
                item.get("name", ""),
            )
        )
        self.warehouse_id = warehouses[0]["id"]
        return self.warehouse_id

    def execute(self, statement: str) -> dict[str, Any]:
        """Execute one SQL statement and wait for its terminal state."""
        warehouse_id = self.ensure_warehouse()
        result = self._request(
            "POST",
            "/api/2.0/sql/statements",
            json={
                "warehouse_id": warehouse_id,
                "statement": statement,
                "wait_timeout": "50s",
                "on_wait_timeout": "CONTINUE",
                "format": "JSON_ARRAY",
                "disposition": "INLINE",
            },
        )

        statement_id = result.get("statement_id")
        state = result.get("status", {}).get("state")
        while state not in TERMINAL_STATUSES:
            if not statement_id:
                raise DeploymentError("Databricks did not return a statement identifier")
            time.sleep(1)
            result = self._request("GET", f"/api/2.0/sql/statements/{statement_id}")
            state = result.get("status", {}).get("state")

        if state != "SUCCEEDED":
            error = result.get("status", {}).get("error", {})
            message = error.get("message", "SQL statement failed without an error message")
            raise DeploymentError(message)
        return result


def first_value(result: dict[str, Any]) -> str | None:
    """Return the first scalar from an inline statement result."""
    rows = result.get("result", {}).get("data_array", [])
    if not rows or not rows[0]:
        return None
    return str(rows[0][0])


def list_catalogs(client: DatabricksSqlClient) -> set[str]:
    """List catalogs visible to the authenticated principal."""
    result = client.execute("SHOW CATALOGS")
    rows = result.get("result", {}).get("data_array", [])
    return {str(row[0]) for row in rows if row}


def resolve_catalog(client: DatabricksSqlClient, desired_catalog: str) -> tuple[str, bool]:
    """Create the desired catalog, falling back only when creation is unavailable."""
    desired_catalog = validate_identifier(desired_catalog, "catalog")
    visible_catalogs = list_catalogs(client)
    if desired_catalog in visible_catalogs:
        return desired_catalog, False

    try:
        client.execute(f"CREATE CATALOG IF NOT EXISTS {quote_identifier(desired_catalog)}")
        return desired_catalog, True
    except DeploymentError as exc:
        refreshed_catalogs = list_catalogs(client)
        for fallback in ("main", "workspace", "hive_metastore"):
            if fallback in refreshed_catalogs:
                print(
                    f"Catalog creation unavailable ({exc}); using accessible catalog '{fallback}'.",
                    file=sys.stderr,
                )
                return fallback, False
        current_catalog = first_value(client.execute("SELECT current_catalog()"))
        if current_catalog and current_catalog in refreshed_catalogs:
            return current_catalog, False
        raise DeploymentError(
            f"Could not create catalog '{desired_catalog}' and no safe fallback is accessible"
        ) from exc


def deploy_foundation(client: DatabricksSqlClient) -> dict[str, Any]:
    """Deploy and validate all FuelSignal medallion objects."""
    desired_catalog = os.environ.get("DATABRICKS_CATALOG", "").strip() or "fuelsignal"
    schema_prefix = validate_identifier(
        os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal"), "schema prefix"
    )
    catalog, catalog_created = resolve_catalog(client, desired_catalog)
    catalog = validate_identifier(catalog, "catalog")

    schema_names = {
        layer: f"{schema_prefix}_{layer}" for layer in ("bronze", "silver", "gold", "monitoring")
    }
    for schema_name in schema_names.values():
        validate_identifier(schema_name, "schema")
        client.execute(
            f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(catalog)}."
            f"{quote_identifier(schema_name)}"
        )

    qualified_schemas = {
        layer: f"{catalog}.{schema_name}" for layer, schema_name in schema_names.items()
    }
    ddl_groups = {
        "bronze": get_bronze_ddl(qualified_schemas["bronze"]),
        "silver": get_silver_ddl(qualified_schemas["silver"]),
        "gold": get_gold_ddl(qualified_schemas["gold"]),
        "monitoring": get_monitoring_ddl(qualified_schemas["monitoring"]),
    }

    created_tables: list[str] = []
    for layer, statements in ddl_groups.items():
        for table_name, ddl in statements.items():
            client.execute(ddl)
            created_tables.append(f"{qualified_schemas[layer]}.{table_name}")

    validated_tables: dict[str, int] = {}
    for layer, schema_name in qualified_schemas.items():
        result = client.execute(f"SHOW TABLES IN {schema_name}")
        rows = result.get("result", {}).get("data_array", [])
        validated_tables[layer] = len(rows)
        returned_values = {str(value) for row in rows for value in row}
        missing_tables = set(ddl_groups[layer]) - returned_values
        if missing_tables:
            raise DeploymentError(
                f"Missing tables in {schema_name}: {', '.join(sorted(missing_tables))}"
            )

    return {
        "catalog": catalog,
        "catalog_created": catalog_created,
        "schemas": list(qualified_schemas.values()),
        "tables": created_tables,
        "table_counts": validated_tables,
    }


def main() -> int:
    """Load credentials, deploy the foundation, and print a non-secret summary."""
    try:
        config = get_databricks_config()
        client = DatabricksSqlClient(
            host=config["host"],
            token=config["token"],
            warehouse_id=os.environ.get("DATABRICKS_WAREHOUSE_ID") or None,
        )
        summary = deploy_foundation(client)
    except (DeploymentError, OSError, ValueError) as exc:
        print(f"Foundation deployment failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
