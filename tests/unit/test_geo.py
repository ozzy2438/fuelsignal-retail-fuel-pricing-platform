"""Unit tests for geospatial utilities."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from fuelsignal.utils.geo import (
    haversine_distance_km,
    is_within_radius,
    validate_nsw_coordinates,
)


class TestHaversineDistance:
    """Test Haversine distance calculations."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself should be zero."""
        dist = haversine_distance_km(-33.8688, 151.2093, -33.8688, 151.2093)
        assert dist == 0.0

    def test_sydney_to_melbourne(self):
        """Sydney to Melbourne is approximately 714 km."""
        # Sydney: -33.8688, 151.2093
        # Melbourne: -37.8136, 144.9631
        dist = haversine_distance_km(-33.8688, 151.2093, -37.8136, 144.9631)
        assert 700 < dist < 730, f"Expected ~714km, got {dist}km"

    def test_nearby_stations(self):
        """Two nearby stations should be within a few km."""
        # Two points ~1km apart in Sydney
        dist = haversine_distance_km(-33.8688, 151.2093, -33.8770, 151.2100)
        assert 0.5 < dist < 2.0, f"Expected ~1km, got {dist}km"

    def test_symmetric(self):
        """Distance A->B should equal distance B->A."""
        d1 = haversine_distance_km(-33.8688, 151.2093, -33.9, 151.3)
        d2 = haversine_distance_km(-33.9, 151.3, -33.8688, 151.2093)
        assert abs(d1 - d2) < 1e-10

    def test_invalid_latitude_raises(self):
        """Invalid latitude should raise ValueError."""
        with pytest.raises(ValueError, match="Latitude"):
            haversine_distance_km(91.0, 151.0, -33.0, 151.0)

    def test_invalid_longitude_raises(self):
        """Invalid longitude should raise ValueError."""
        with pytest.raises(ValueError, match="Longitude"):
            haversine_distance_km(-33.0, 181.0, -33.0, 151.0)


class TestIsWithinRadius:
    """Test radius-based proximity check."""

    def test_close_stations_within_5km(self):
        """Close stations should be within 5km."""
        # ~1km apart
        assert is_within_radius(-33.8688, 151.2093, -33.8770, 151.2100, 5.0)

    def test_distant_stations_outside_5km(self):
        """Distant stations should not be within 5km."""
        # Sydney to Parramatta (~20km)
        assert not is_within_radius(-33.8688, 151.2093, -33.8150, 151.0011, 5.0)


class TestNSWCoordinates:
    """Test NSW coordinate validation."""

    def test_sydney_valid(self):
        """Sydney coordinates are valid NSW."""
        assert validate_nsw_coordinates(-33.8688, 151.2093)

    def test_melbourne_invalid(self):
        """Melbourne coordinates are NOT in NSW."""
        assert not validate_nsw_coordinates(-37.8136, 144.9631)

    def test_northern_nsw(self):
        """Byron Bay is in NSW."""
        assert validate_nsw_coordinates(-28.6474, 153.6020)

    def test_western_nsw(self):
        """Broken Hill is in NSW."""
        assert validate_nsw_coordinates(-31.9505, 141.4681)
