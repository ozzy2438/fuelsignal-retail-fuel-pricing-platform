"""Unit tests for the Databricks foundation deployer."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "deploy_databricks_foundation.py"
SPEC = importlib.util.spec_from_file_location("deploy_databricks_foundation", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
deployment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deployment
SPEC.loader.exec_module(deployment)


class FakeClient:
    def __init__(self, catalogs: set[str], create_error: Exception | None = None):
        self.catalogs = catalogs
        self.create_error = create_error
        self.statements: list[str] = []

    def execute(self, statement: str) -> dict:
        self.statements.append(statement)
        if statement == "SHOW CATALOGS":
            return {"result": {"data_array": [[name] for name in sorted(self.catalogs)]}}
        if statement.startswith("CREATE CATALOG"):
            if self.create_error:
                raise self.create_error
            self.catalogs.add("fuelsignal")
            return {"status": {"state": "SUCCEEDED"}}
        raise AssertionError(f"Unexpected SQL: {statement}")


@pytest.mark.unit()
def test_validate_identifier_rejects_sql_injection() -> None:
    with pytest.raises(ValueError, match="Invalid catalog"):
        deployment.validate_identifier("main; DROP CATALOG main", "catalog")


@pytest.mark.unit()
def test_resolve_catalog_reuses_existing_catalog() -> None:
    client = FakeClient({"fuelsignal", "main"})

    catalog, created = deployment.resolve_catalog(client, "fuelsignal")

    assert (catalog, created) == ("fuelsignal", False)
    assert client.statements == ["SHOW CATALOGS"]


@pytest.mark.unit()
def test_resolve_catalog_creates_missing_catalog() -> None:
    client = FakeClient({"main"})

    catalog, created = deployment.resolve_catalog(client, "fuelsignal")

    assert (catalog, created) == ("fuelsignal", True)


@pytest.mark.unit()
def test_resolve_catalog_falls_back_to_main() -> None:
    client = FakeClient({"main"}, deployment.DeploymentError("permission denied"))

    catalog, created = deployment.resolve_catalog(client, "fuelsignal")

    assert (catalog, created) == ("main", False)
