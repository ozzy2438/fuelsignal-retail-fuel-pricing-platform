"""Geospatial utility functions.

Implements Haversine distance calculation for competitor set determination.
"""

import math

# Earth's mean radius in kilometres
EARTH_RADIUS_KM = 6371.0


def haversine_distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Calculate the great-circle distance between two points on Earth.

    Uses the Haversine formula to compute distance between two
    latitude/longitude coordinate pairs.

    Args:
        lat1: Latitude of point 1 in decimal degrees.
        lon1: Longitude of point 1 in decimal degrees.
        lat2: Latitude of point 2 in decimal degrees.
        lon2: Longitude of point 2 in decimal degrees.

    Returns:
        Distance in kilometres.

    Raises:
        ValueError: If coordinates are outside valid ranges.

    Example:
        >>> haversine_distance_km(-33.8688, 151.2093, -33.8800, 151.2100)
        1.245...  # approximately 1.2 km
    """
    # Validate inputs
    for lat in (lat1, lat2):
        if not -90 <= lat <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    for lon in (lon1, lon2):
        if not -180 <= lon <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got {lon}")

    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    # Haversine formula
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def is_within_radius(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    radius_km: float = 5.0,
) -> bool:
    """Check if two points are within a specified radius.

    Args:
        lat1, lon1: Coordinates of first point.
        lat2, lon2: Coordinates of second point.
        radius_km: Maximum distance in km (default: 5.0).

    Returns:
        True if distance <= radius_km.
    """
    return haversine_distance_km(lat1, lon1, lat2, lon2) <= radius_km


def validate_nsw_coordinates(lat: float, lon: float) -> bool:
    """Validate that coordinates are within NSW bounding box.

    NSW approximate bounds:
    - Latitude: -37.5 to -28.0
    - Longitude: 141.0 to 154.0

    Returns:
        True if coordinates are within NSW bounds.
    """
    return -37.5 <= lat <= -28.0 and 141.0 <= lon <= 154.0
