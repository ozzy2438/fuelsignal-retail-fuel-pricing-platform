"""Unit tests for validation utilities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from fuelsignal.utils.validation import (
    normalize_fuel_type,
    validate_coordinates_nsw,
    validate_price_bounds,
    validate_url,
)


class TestValidateURL:
    """Test URL validation."""

    def test_valid_https_url(self):
        assert validate_url("https://data.nsw.gov.au/data/dataset/fuel-check")

    def test_valid_http_url(self):
        assert validate_url("http://example.com")

    def test_empty_string_invalid(self):
        assert not validate_url("")

    def test_none_invalid(self):
        assert not validate_url(None)

    def test_no_scheme_invalid(self):
        assert not validate_url("data.nsw.gov.au")

    def test_ftp_invalid(self):
        assert not validate_url("ftp://files.example.com")


class TestValidatePriceBounds:
    """Test price boundary validation."""

    def test_normal_price_valid(self):
        is_valid, msg = validate_price_bounds(165.9)
        assert is_valid
        assert msg is None

    def test_price_too_low(self):
        is_valid, msg = validate_price_bounds(50.0)
        assert not is_valid
        assert "below minimum" in msg

    def test_price_too_high(self):
        is_valid, msg = validate_price_bounds(350.0)
        assert not is_valid
        assert "above maximum" in msg

    def test_boundary_price_valid(self):
        is_valid, _ = validate_price_bounds(80.0)
        assert is_valid
        is_valid, _ = validate_price_bounds(300.0)
        assert is_valid

    def test_null_price_invalid(self):
        is_valid, msg = validate_price_bounds(None)
        assert not is_valid
        assert "null" in msg.lower()

    def test_custom_bounds(self):
        is_valid, _ = validate_price_bounds(50.0, min_cpl=40.0, max_cpl=60.0)
        assert is_valid


class TestValidateCoordinatesNSW:
    """Test NSW coordinate validation."""

    def test_sydney_valid(self):
        is_valid, msg = validate_coordinates_nsw(-33.8688, 151.2093)
        assert is_valid

    def test_null_latitude(self):
        is_valid, msg = validate_coordinates_nsw(None, 151.0)
        assert not is_valid

    def test_outside_nsw_south(self):
        is_valid, msg = validate_coordinates_nsw(-40.0, 150.0)
        assert not is_valid


class TestNormalizeFuelType:
    """Test fuel type normalization."""

    def test_e10_variants(self):
        assert normalize_fuel_type("E10") == "E10"
        assert normalize_fuel_type("Ethanol 10") == "E10"
        assert normalize_fuel_type("e10") == "E10"

    def test_ulp_variants(self):
        assert normalize_fuel_type("ULP") == "U91"
        assert normalize_fuel_type("Unleaded") == "U91"
        assert normalize_fuel_type("Regular Unleaded") == "U91"
        assert normalize_fuel_type("Unleaded 91") == "U91"

    def test_premium_95(self):
        assert normalize_fuel_type("P95") == "P95"
        assert normalize_fuel_type("Premium 95") == "P95"
        assert normalize_fuel_type("PULP 95") == "P95"

    def test_premium_98(self):
        assert normalize_fuel_type("P98") == "P98"
        assert normalize_fuel_type("Premium 98") == "P98"

    def test_diesel(self):
        assert normalize_fuel_type("Diesel") == "DIESEL"
        assert normalize_fuel_type("DL") == "DIESEL"

    def test_lpg(self):
        assert normalize_fuel_type("LPG") == "LPG"
        assert normalize_fuel_type("Autogas") == "LPG"

    def test_unknown_passthrough(self):
        assert normalize_fuel_type("SOME_NEW_FUEL") == "SOME_NEW_FUEL"

    def test_empty_returns_unknown(self):
        assert normalize_fuel_type("") == "UNKNOWN"
        assert normalize_fuel_type(None) == "UNKNOWN"
