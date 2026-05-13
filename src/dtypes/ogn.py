"""API response schemas for OGN data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ObservationOut(BaseModel):
    """Parsed OGN observation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    aircraft_id: str | None
    beacon_type: str | None
    receiver_name: str | None
    timestamp: str | None
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    ground_speed_kmh: float | None
    track_deg: float | None


class CountsOut(BaseModel):
    """Primary database table counts."""

    raw_messages: int
    position_observations: int
    cleaned_observations: int
    track_segments: int
    track_points: int


class CleanedObservationOut(BaseModel):
    """Processed OGN observation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    aircraft_id: str
    beacon_type: str | None
    aircraft_type: int | None
    aircraft_type_name: str
    timestamp: str
    latitude: float
    longitude: float
    altitude_m: float | None
    ground_speed_kmh: float | None
    track_deg: float | None
    inside_swiss_bbox: int
    is_likely_unconventional: int
    quality_flags: str


class TrackSegmentOut(BaseModel):
    """Processed track segment returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    aircraft_id: str
    aircraft_type: int | None
    aircraft_type_name: str
    beacon_type: str | None
    start_timestamp: str
    end_timestamp: str
    n_points: int
    duration_s: float
    max_gap_s: float
    distance_km: float
    min_altitude_m: float | None
    max_altitude_m: float | None
    avg_ground_speed_kmh: float | None
    max_ground_speed_kmh: float | None
    is_likely_unconventional: int


class HealthOut(BaseModel):
    """Application health response."""

    status: str
    database_url: str
