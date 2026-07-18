"""Tests for the live official-source ingestion pipeline."""

import importlib.util
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_ingestion_pipeline.py"
SCRIPTS_DIR = str(SCRIPT_PATH.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
SPEC = importlib.util.spec_from_file_location("run_ingestion_pipeline", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


@pytest.mark.unit
def test_sql_literal_escapes_single_quotes() -> None:
    assert pipeline.sql_literal("King's Birthday") == "'King''s Birthday'"
    assert pipeline.sql_literal(None) == "NULL"


@pytest.mark.unit
def test_parse_aip_workbook_normalizes_city_rows() -> None:
    output = io.BytesIO()
    petrol = pd.DataFrame(
        {
            "AVERAGE ULP TGPS\n(inclusive of GST)": [pd.Timestamp("2026-07-17")],
            "Sydney": [167.2],
            "National\nAverage": [166.0],
        }
    )
    diesel = pd.DataFrame(
        {
            "AVERAGE DIESEL TGPS\n(inclusive of GST)": [pd.Timestamp("2026-07-17")],
            "Sydney": [202.0],
            "National\nAverage": [200.0],
        }
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        petrol.to_excel(writer, sheet_name="Petrol TGP", index=False)
        diesel.to_excel(writer, sheet_name="Diesel TGP", index=False)

    result = pipeline.parse_aip_workbook(output.getvalue())

    assert result[["product", "city", "price_cpl"]].to_dict("records") == [
        {"product": "ULP", "city": "Sydney", "price_cpl": 167.2},
        {"product": "Diesel", "city": "Sydney", "price_cpl": 202.0},
    ]


@pytest.mark.unit
def test_parse_fuelcheck_month_reads_csv() -> None:
    frame = pd.DataFrame(
        {
            "ServiceStationName": ["Test Station"],
            "Address": ["1 Test St"],
            "Suburb": ["TESTVILLE"],
            "Postcode": ["2000"],
            "Brand": ["Shell"],
            "FuelCode": ["U91"],
            "PriceUpdatedDate": ["2026-01-01T00:00:00"],
            "Price": [180.5],
        }
    )
    content = frame.to_csv(index=False).encode("utf-8")

    result = pipeline.parse_fuelcheck_month(content, "price_history_checks_jan2026.csv")

    assert list(result.columns) == pipeline.FUELCHECK_MONTH_COLUMNS
    assert result.iloc[0]["ServiceStationName"] == "Test Station"


@pytest.mark.unit
def test_parse_fuelcheck_month_reads_xlsx() -> None:
    frame = pd.DataFrame(
        {
            "ServiceStationName": ["Test Station"],
            "Address": ["1 Test St"],
            "Suburb": ["TESTVILLE"],
            "Postcode": ["2000"],
            "Brand": ["Shell"],
            "FuelCode": ["U91"],
            "PriceUpdatedDate": ["2026-01-01T00:00:00"],
            "Price": [180.5],
        }
    )
    output = io.BytesIO()
    frame.to_excel(output, index=False, engine="openpyxl")

    result = pipeline.parse_fuelcheck_month(
        output.getvalue(), "fuelcheck_pricehistory_jan2026.xlsx"
    )

    assert list(result.columns) == pipeline.FUELCHECK_MONTH_COLUMNS


@pytest.mark.unit
def test_parse_fuelcheck_month_normalizes_xlsx_datetime_to_iso_string() -> None:
    # XLSX resources parse PriceUpdatedDate as a real datetime64 column. Left as-is,
    # pandas.to_json serializes datetime64 columns as epoch milliseconds instead of an
    # ISO string, which silently breaks every downstream try_cast(... AS TIMESTAMP).
    frame = pd.DataFrame(
        {
            "ServiceStationName": ["Test Station"],
            "Address": ["1 Test St"],
            "Suburb": ["TESTVILLE"],
            "Postcode": ["2000"],
            "Brand": ["Shell"],
            "FuelCode": ["U91"],
            "PriceUpdatedDate": [pd.Timestamp("2025-04-01 12:00:00")],
            "Price": [180.5],
        }
    )
    output = io.BytesIO()
    frame.to_excel(output, index=False, engine="openpyxl")

    result = pipeline.parse_fuelcheck_month(
        output.getvalue(), "fuelcheck_pricehistory_apr2025.xlsx"
    )

    assert result.iloc[0]["PriceUpdatedDate"] == "2025-04-01T12:00:00"
    assert isinstance(result.iloc[0]["PriceUpdatedDate"], str)
    staged = pipeline.dataframe_jsonl_bytes(result)
    assert b"1743" not in staged  # no epoch-millisecond leakage into the staged JSONL


@pytest.mark.unit
def test_parse_fuelcheck_month_rejects_missing_columns() -> None:
    frame = pd.DataFrame({"ServiceStationName": ["Test Station"]})
    content = frame.to_csv(index=False).encode("utf-8")

    with pytest.raises(RuntimeError, match="missing expected columns"):
        pipeline.parse_fuelcheck_month(content, "price_history_checks_jan2026.csv")


@pytest.mark.unit
def test_parse_fuelcheck_month_rejects_unsupported_format() -> None:
    with pytest.raises(RuntimeError, match="Unsupported FuelCheck resource format"):
        pipeline.parse_fuelcheck_month(b"whatever", "prices.pdf")


@pytest.mark.unit
def test_normalize_key_sql_is_symmetric_with_python_normalizer() -> None:
    sql_expr = pipeline.normalize_key_sql("address", "postcode")
    assert "regexp_replace" in sql_expr
    assert "upper(coalesce(address" in sql_expr
    assert "upper(coalesce(postcode" in sql_expr


class _FakeDescribeClient:
    """Minimal fake DatabricksSqlClient for ensure_columns migration tests."""

    def __init__(self, existing_columns: list[str]):
        self.existing_columns = existing_columns
        self.statements: list[str] = []
        self.host = "https://example.databricks.com"
        self.token = "fake"  # noqa: S105

    def execute(self, statement: str) -> dict:
        self.statements.append(statement)
        if statement.startswith("DESCRIBE TABLE"):
            return {
                "result": {"data_array": [[name, "STRING", None] for name in self.existing_columns]}
            }
        return {"result": {"data_array": []}}


@pytest.mark.unit
def test_ensure_columns_adds_only_missing_columns() -> None:
    client = _FakeDescribeClient(existing_columns=["station_id", "station_name"])
    live = pipeline.LivePipeline(client, "test-run-id")

    live.ensure_columns("catalog.schema.table", {"station_id": "STRING", "new_col": "DOUBLE"})

    alter_statements = [s for s in client.statements if s.startswith("ALTER TABLE")]
    assert len(alter_statements) == 1
    assert "new_col DOUBLE" in alter_statements[0]
    assert "station_id" not in alter_statements[0].split("ADD COLUMNS")[1]


@pytest.mark.unit
def test_ensure_columns_is_noop_when_all_columns_exist() -> None:
    client = _FakeDescribeClient(existing_columns=["station_id", "new_col"])
    live = pipeline.LivePipeline(client, "test-run-id")

    live.ensure_columns("catalog.schema.table", {"station_id": "STRING", "new_col": "DOUBLE"})

    assert not [s for s in client.statements if s.startswith("ALTER TABLE")]


@pytest.mark.unit
def test_parse_holiday_page_skips_not_applicable_and_cleans_footnotes() -> None:
    html = b"""
    <table>
      <thead><tr><th>Holiday</th><th>2026</th><th>2027</th></tr></thead>
      <tbody>
                <tr><td>2Australia Day</td><td>Monday 26 January 2026</td>
                    <td>Tuesday 26 January 2027</td></tr>
        <tr><td>3Additional Day</td><td>Not applicable</td><td>Monday 27 December 2027</td></tr>
      </tbody>
    </table>
    """

    result = pipeline.parse_holiday_page(html)

    assert result[["date", "holiday_name"]].to_dict("records") == [
        {"date": "2026-01-26", "holiday_name": "Australia Day"},
        {"date": "2027-01-26", "holiday_name": "Australia Day"},
        {"date": "2027-12-27", "holiday_name": "Additional Day"},
    ]
