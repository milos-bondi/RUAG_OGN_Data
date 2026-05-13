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


class HealthOut(BaseModel):
    """Application health response."""

    status: str
    database_url: str
