"""SQLAlchemy models for collected OGN messages."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for ORM models."""


class RawMessage(Base):
    """Raw APRS message received from the OGN stream."""

    __tablename__ = "raw_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_at: Mapped[str] = mapped_column(String, nullable=False)
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    parse_status: Mapped[str] = mapped_column(String, nullable=False)
    parse_error: Mapped[str] = mapped_column(Text, nullable=True)
    positions: Mapped[list["PositionObservation"]] = relationship(back_populates="raw")


class PositionObservation(Base):
    """Parsed aircraft position observation."""

    __tablename__ = "position_observations"
    __table_args__ = (
        Index("idx_position_time", "timestamp"),
        Index("idx_position_aircraft_time", "aircraft_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_message_id: Mapped[int] = mapped_column(ForeignKey("raw_messages.id"), nullable=False)
    aircraft_id: Mapped[str] = mapped_column(String, nullable=True)
    device_address: Mapped[str] = mapped_column(String, nullable=True)
    beacon_type: Mapped[str] = mapped_column(String, nullable=True)
    receiver_name: Mapped[str] = mapped_column(String, nullable=True)
    timestamp: Mapped[str] = mapped_column(String, nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=True)
    longitude: Mapped[float] = mapped_column(Float, nullable=True)
    altitude_m: Mapped[float] = mapped_column(Float, nullable=True)
    ground_speed_kmh: Mapped[float] = mapped_column(Float, nullable=True)
    track_deg: Mapped[float] = mapped_column(Float, nullable=True)
    climb_rate_ms: Mapped[float] = mapped_column(Float, nullable=True)
    turn_rate_degs: Mapped[float] = mapped_column(Float, nullable=True)
    aircraft_type: Mapped[int] = mapped_column(Integer, nullable=True)
    no_tracking: Mapped[int] = mapped_column(Integer, nullable=True)
    stealth: Mapped[int] = mapped_column(Integer, nullable=True)
    raw: Mapped[RawMessage] = relationship(back_populates="positions")


class ProcessingState(Base):
    """Incremental processing key/value state."""

    __tablename__ = "processing_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=True)


class CleanedObservation(Base):
    """Quality-filtered observation ready for analytics and tracks."""

    __tablename__ = "cleaned_observations"
    __table_args__ = (
        Index("idx_cleaned_aircraft_time", "aircraft_id", "timestamp"),
        Index("idx_cleaned_type_time", "aircraft_type", "timestamp"),
        Index("idx_cleaned_bbox", "inside_swiss_bbox"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_observation_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    raw_message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    aircraft_id: Mapped[str] = mapped_column(String, nullable=False)
    device_address: Mapped[str] = mapped_column(String, nullable=True)
    beacon_type: Mapped[str] = mapped_column(String, nullable=True)
    aircraft_type: Mapped[int] = mapped_column(Integer, nullable=True)
    aircraft_type_name: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_m: Mapped[float] = mapped_column(Float, nullable=True)
    ground_speed_kmh: Mapped[float] = mapped_column(Float, nullable=True)
    track_deg: Mapped[float] = mapped_column(Float, nullable=True)
    climb_rate_ms: Mapped[float] = mapped_column(Float, nullable=True)
    turn_rate_degs: Mapped[float] = mapped_column(Float, nullable=True)
    inside_swiss_bbox: Mapped[int] = mapped_column(Integer, nullable=False)
    is_likely_unconventional: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_flags: Mapped[str] = mapped_column(Text, nullable=False)


class TrackSegment(Base):
    """Continuous track segment built from cleaned observations."""

    __tablename__ = "track_segments"
    __table_args__ = (
        Index("idx_segments_aircraft_time", "aircraft_id", "start_timestamp", "end_timestamp"),
        Index("idx_segments_points_duration", "n_points", "duration_s"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aircraft_id: Mapped[str] = mapped_column(String, nullable=False)
    aircraft_type: Mapped[int] = mapped_column(Integer, nullable=True)
    aircraft_type_name: Mapped[str] = mapped_column(String, nullable=False)
    beacon_type: Mapped[str] = mapped_column(String, nullable=True)
    start_cleaned_observation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    end_cleaned_observation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    start_timestamp: Mapped[str] = mapped_column(String, nullable=False)
    end_timestamp: Mapped[str] = mapped_column(String, nullable=False)
    n_points: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    max_gap_s: Mapped[float] = mapped_column(Float, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    min_altitude_m: Mapped[float] = mapped_column(Float, nullable=True)
    max_altitude_m: Mapped[float] = mapped_column(Float, nullable=True)
    avg_ground_speed_kmh: Mapped[float] = mapped_column(Float, nullable=True)
    max_ground_speed_kmh: Mapped[float] = mapped_column(Float, nullable=True)
    is_likely_unconventional: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_reason: Mapped[str] = mapped_column(String, nullable=True)
    points: Mapped[list["TrackPoint"]] = relationship(back_populates="segment")


class TrackPoint(Base):
    """One cleaned observation assigned to a track segment."""

    __tablename__ = "track_points"
    __table_args__ = (
        Index("idx_track_points_segment", "segment_id", "sequence_index"),
        Index("idx_track_points_dt", "dt_s"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("track_segments.id"), nullable=False)
    cleaned_observation_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    dt_s: Mapped[float] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_ground_speed_kmh: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_heading_deg: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_climb_rate_ms: Mapped[float] = mapped_column(Float, nullable=True)
    segment: Mapped[TrackSegment] = relationship(back_populates="points")
