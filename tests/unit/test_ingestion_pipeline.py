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


@pytest.mark.unit()
def test_sql_literal_escapes_single_quotes() -> None:
    assert pipeline.sql_literal("King's Birthday") == "'King''s Birthday'"
    assert pipeline.sql_literal(None) == "NULL"


@pytest.mark.unit()
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


@pytest.mark.unit()
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
