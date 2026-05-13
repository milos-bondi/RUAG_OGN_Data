"""Geographic and OGN aircraft helper functions."""

from __future__ import annotations

import math
from datetime import datetime


SWITZERLAND_BBOX = {
    "min_lat": 45.75,
    "max_lat": 47.85,
    "min_lon": 5.85,
    "max_lon": 10.65,
}

UNCONVENTIONAL_AIRCRAFT_TYPES = {1, 4, 6, 7, 11, 12, 13}

AIRCRAFT_TYPE_NAMES = {
    None: "unknown",
    0: "Unknown",
    1: "Glider/Motor Glider",
    2: "Tow/Tug Plane",
    3: "Helicopter/Rotorcraft",
    4: "Skydiver",
    5: "Drop Plane",
    6: "Hang Glider",
    7: "Paraglider",
    8: "Powered Aircraft",
    9: "Jet Aircraft",
    10: "UFO/Other",
    11: "Balloon",
    12: "Airship",
    13: "UAV/Drone",
    14: "Static Object",
    15: "Other/Reserved",
}


def aircraft_type_name(code: int | None) -> str:
    """Return a readable OGN aircraft type label."""
    return AIRCRAFT_TYPE_NAMES.get(code, f"Type {code}")


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp into a datetime when possible."""
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    """Return seconds between two datetimes."""
    if not start or not end:
        return None

    return (end - start).total_seconds()


def inside_swiss_bbox(latitude: float | None, longitude: float | None) -> bool:
    """Return whether a coordinate is inside the configured Switzerland bbox."""
    if latitude is None or longitude is None:
        return False

    return (
        SWITZERLAND_BBOX["min_lat"] <= latitude <= SWITZERLAND_BBOX["max_lat"]
        and SWITZERLAND_BBOX["min_lon"] <= longitude <= SWITZERLAND_BBOX["max_lon"]
    )


def is_likely_unconventional_type(aircraft_type: int | None) -> bool:
    """Return whether an OGN aircraft type is in the unconventional profile."""
    return aircraft_type in UNCONVENTIONAL_AIRCRAFT_TYPES


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in meters."""
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    # Haversine stays stable for short aircraft position deltas.
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return initial bearing in degrees."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)

    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def speed_limit_for_type(aircraft_type: int | None) -> float:
    """Return a coarse maximum plausible speed for an OGN aircraft type."""
    if aircraft_type in {6, 7, 11, 12}:
        return 180.0

    if aircraft_type in {1, 4, 13}:
        return 300.0

    if aircraft_type in {2, 3, 8}:
        return 450.0

    return 1200.0


def altitude_band(altitude_m: float | None) -> str:
    """Return a coarse altitude band label."""
    if altitude_m is None:
        return "unknown"

    for upper, label in [
        (500, "0-500 m"),
        (1000, "500-1000 m"),
        (1500, "1000-1500 m"),
        (2000, "1500-2000 m"),
        (3000, "2000-3000 m"),
        (5000, "3000-5000 m"),
    ]:
        if altitude_m < upper:
            return label

    return ">5000 m"


def cell_for(latitude: float, longitude: float, cell_size_deg: float) -> tuple[float, float]:
    """Return a grid cell origin for a coordinate."""
    lat_cell = math.floor(latitude / cell_size_deg) * cell_size_deg
    lon_cell = math.floor(longitude / cell_size_deg) * cell_size_deg

    return (round(lat_cell, 6), round(lon_cell, 6))


def cell_center(cell: tuple[float, float], cell_size_deg: float) -> tuple[float, float]:
    """Return the center coordinate for a grid cell."""
    return (cell[0] + cell_size_deg / 2.0, cell[1] + cell_size_deg / 2.0)
