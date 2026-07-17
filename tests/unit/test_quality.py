"""Unit tests for data quality checks."""

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from fuelsignal.quality.checks import (
    check_not_null,
    check_price_bounds,
    check_coordinates_nsw,
    check_duplicates,
)


class TestCheckNotNull:
    """Test not-null quality check."""
    
    def test_all_valid(self):
        records = [
            {"station_id": "S001", "price": 165.9},
            {"station_id": "S002", "price": 170.5},
        ]
        result = check_not_null(records, "station_id")
        assert result.failed_records == 0
        assert result.pass_rate == 1.0
    
    def test_null_detected(self):
        records = [
            {"station_id": "S001"},
            {"station_id": None},
            {"station_id": ""},
        ]
        result = check_not_null(records, "station_id")
        assert result.failed_records == 2  # None and empty string
    
    def test_missing_column(self):
        records = [{"other_col": "value"}]
        result = check_not_null(records, "station_id")
        assert result.failed_records == 1


class TestCheckPriceBounds:
    """Test price bounds quality check."""
    
    def test_valid_prices(self):
        records = [
            {"station_id": "S001", "price_cpl": 165.9},
            {"station_id": "S002", "price_cpl": 180.0},
        ]
        result = check_price_bounds(records)
        assert result.failed_records == 0
    
    def test_price_too_low(self):
        records = [{"station_id": "S001", "price_cpl": 50.0}]
        result = check_price_bounds(records)
        assert result.failed_records == 1
    
    def test_price_too_high(self):
        records = [{"station_id": "S001", "price_cpl": 500.0}]
        result = check_price_bounds(records)
        assert result.failed_records == 1
    
    def test_non_numeric_price(self):
        records = [{"station_id": "S001", "price_cpl": "not_a_number"}]
        result = check_price_bounds(records)
        assert result.failed_records == 1


class TestCheckDuplicates:
    """Test duplicate detection."""
    
    def test_no_duplicates(self):
        records = [
            {"station_id": "S001", "fuel_type": "E10", "date": "2024-01-01"},
            {"station_id": "S001", "fuel_type": "E10", "date": "2024-01-02"},
        ]
        result = check_duplicates(records, ["station_id", "fuel_type", "date"])
        assert result.failed_records == 0
    
    def test_duplicates_found(self):
        records = [
            {"station_id": "S001", "fuel_type": "E10", "date": "2024-01-01"},
            {"station_id": "S001", "fuel_type": "E10", "date": "2024-01-01"},
        ]
        result = check_duplicates(records, ["station_id", "fuel_type", "date"])
        assert result.failed_records == 1
