"""Queries for OGN observations and dashboard statistics."""

from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.db.models.ogn import PositionObservation, RawMessage


def count_rows(session: Session) -> dict[str, int]:
    """Return primary table counts."""
    raw_count = session.scalar(select(func.count(RawMessage.id))) or 0
    position_count = session.scalar(select(func.count(PositionObservation.id))) or 0

    return {
        "raw_messages": raw_count,
        "position_observations": position_count,
    }


def latest_observations(session: Session, limit: int) -> list[PositionObservation]:
    """Return the most recent parsed observations."""
    statement = (
        select(PositionObservation)
        .where(PositionObservation.latitude.is_not(None))
        .where(PositionObservation.longitude.is_not(None))
        .order_by(desc(PositionObservation.id))
        .limit(limit)
    )

    return list(session.scalars(statement))


def top_aircraft(session: Session, limit: int) -> list[dict[str, int | str]]:
    """Return aircraft IDs with the largest number of observations."""
    statement = (
        select(PositionObservation.aircraft_id, func.count(PositionObservation.id))
        .where(PositionObservation.aircraft_id.is_not(None))
        .group_by(PositionObservation.aircraft_id)
        .order_by(desc(func.count(PositionObservation.id)))
        .limit(limit)
    )

    return [
        {"aircraft_id": aircraft_id or "", "observations": count}
        for aircraft_id, count in session.execute(statement)
    ]


def beacon_counts(session: Session) -> list[dict[str, int | str]]:
    """Return observation counts grouped by beacon type."""
    statement = (
        select(PositionObservation.beacon_type, func.count(PositionObservation.id))
        .group_by(PositionObservation.beacon_type)
        .order_by(desc(func.count(PositionObservation.id)))
    )

    return [
        {"beacon_type": beacon_type or "unknown", "observations": count}
        for beacon_type, count in session.execute(statement)
    ]
