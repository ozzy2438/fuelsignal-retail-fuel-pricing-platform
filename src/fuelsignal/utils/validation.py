"""Validation utility functions."""

from urllib.parse import urlparse


def validate_url(url: str) -> bool:
    """Validate that a string is a properly formed URL.

    Args:
        url: URL string to validate.

    Returns:
        True if the URL has a valid scheme and netloc.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        result = urlparse(url.strip())
        return all([result.scheme in ("http", "https"), result.netloc])
    except (ValueError, AttributeError):
        return False


def validate_price_bounds(
    price: float,
    min_cpl: float = 80.0,
    max_cpl: float = 300.0,
) -> tuple[bool, str | None]:
    """Validate that a fuel price is within plausible bounds.

    Args:
        price: Price in cents per litre.
        min_cpl: Minimum plausible price.
        max_cpl: Maximum plausible price.

    Returns:
        Tuple of (is_valid, error_message_or_none)
    """
    if price is None:
        return False, "Price is null"
    if not isinstance(price, int | float):
        return False, f"Price is not numeric: {type(price).__name__}"
    if price < min_cpl:
        return False, f"Price {price} below minimum {min_cpl} cpl"
    if price > max_cpl:
        return False, f"Price {price} above maximum {max_cpl} cpl"
    return True, None


def validate_coordinates_nsw(
    latitude: float,
    longitude: float,
) -> tuple[bool, str | None]:
    """Validate coordinates are within NSW boundaries.

    NSW approximate bounding box:
    - Latitude: -37.5 to -28.0 (south to north)
    - Longitude: 141.0 to 154.0 (west to east)

    Args:
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.

    Returns:
        Tuple of (is_valid, error_message_or_none)
    """
    if latitude is None or longitude is None:
        return False, "Coordinates contain null values"

    if not (-37.5 <= latitude <= -28.0):
        return False, f"Latitude {latitude} outside NSW range [-37.5, -28.0]"

    if not (141.0 <= longitude <= 154.0):
        return False, f"Longitude {longitude} outside NSW range [141.0, 154.0]"

    return True, None


def normalize_fuel_type(raw_fuel_type: str) -> str:
    """Normalize fuel type names across different sources.

    Maps various naming conventions to a canonical fuel type.

    Args:
        raw_fuel_type: Raw fuel type string from source.

    Returns:
        Normalized canonical fuel type name.
    """
    if not raw_fuel_type:
        return "UNKNOWN"

    normalized = raw_fuel_type.strip().upper()

    # Mapping table for common variations
    fuel_type_map = {
        # Unleaded Petrol (ULP/E10)
        "E10": "E10",
        "ETHANOL 10": "E10",
        "E10 UNLEADED": "E10",
        # Regular Unleaded
        "U91": "U91",
        "ULP": "U91",
        "UNLEADED": "U91",
        "REGULAR UNLEADED": "U91",
        "UNLEADED 91": "U91",
        "UNLEADED PETROL": "U91",
        # Premium 95
        "P95": "P95",
        "PULP 95": "P95",
        "PREMIUM 95": "P95",
        "PREMIUM UNLEADED 95": "P95",
        "UNLEADED 95": "P95",
        # Premium 98
        "P98": "P98",
        "PULP 98": "P98",
        "PREMIUM 98": "P98",
        "PREMIUM UNLEADED 98": "P98",
        "UNLEADED 98": "P98",
        # Diesel
        "DIESEL": "DIESEL",
        "DL": "DIESEL",
        "PREMIUM DIESEL": "DIESEL_PREMIUM",
        # LPG
        "LPG": "LPG",
        "AUTOGAS": "LPG",
        # E85
        "E85": "E85",
        "ETHANOL 85": "E85",
    }

    return fuel_type_map.get(normalized, normalized)
