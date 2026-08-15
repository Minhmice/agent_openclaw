"""Great-circle distance calculations."""

import math

# IUGG mean Earth radius. A fixed radius makes results reproducible.
EARTH_RADIUS_KM = 6371.0088


def _coordinate(value: float, *, name: str, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a finite real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if not lower <= result <= upper:
        raise ValueError(f"{name} must be between {lower:g} and {upper:g}")
    return result


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return great-circle distance in km using the 6371.0088 km IUGG mean radius."""

    lat_a = math.radians(_coordinate(latitude_a, name="latitude_a", lower=-90, upper=90))
    lon_a = math.radians(_coordinate(longitude_a, name="longitude_a", lower=-180, upper=180))
    lat_b = math.radians(_coordinate(latitude_b, name="latitude_b", lower=-90, upper=90))
    lon_b = math.radians(_coordinate(longitude_b, name="longitude_b", lower=-180, upper=180))
    delta_lat = lat_b - lat_a
    # Reduce across the antimeridian to the shortest signed angular difference.
    delta_lon = (lon_b - lon_a + math.pi) % (2 * math.pi) - math.pi
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    central_angle = 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))
    return EARTH_RADIUS_KM * central_angle
