"""SQLAlchemy models for collected OGN messages."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
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
    parse_error: Mapped[str] = mapped_column(Text)
    positions: Mapped[list["PositionObservation"]] = relationship(back_populates="raw")


class PositionObservation(Base):
    """Parsed aircraft position observation."""

    __tablename__ = "position_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_message_id: Mapped[int] = mapped_column(ForeignKey("raw_messages.id"), nullable=False)
    aircraft_id: Mapped[str] = mapped_column(String)
    device_address: Mapped[str] = mapped_column(String)
    beacon_type: Mapped[str] = mapped_column(String)
    receiver_name: Mapped[str] = mapped_column(String)
    timestamp: Mapped[str] = mapped_column(String)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float)
    ground_speed_kmh: Mapped[float] = mapped_column(Float)
    track_deg: Mapped[float] = mapped_column(Float)
    climb_rate_ms: Mapped[float] = mapped_column(Float)
    turn_rate_degs: Mapped[float] = mapped_column(Float)
    aircraft_type: Mapped[int] = mapped_column(Integer)
    no_tracking: Mapped[int] = mapped_column(Integer)
    stealth: Mapped[int] = mapped_column(Integer)
    raw: Mapped[RawMessage] = relationship(back_populates="positions")
