"""Queries for OGN observations and dashboard statistics."""

from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.db.models.ogn import CleanedObservation, PositionObservation, RawMessage, TrackPoint
from src.db.models.ogn import TrackSegment
from src.utils.geo import SWITZERLAND_BBOX


def count_rows(session: Session) -> dict[str, int]:
    """Return primary table counts."""
    return {
        "raw_messages": session.scalar(select(func.count(RawMessage.id))) or 0,
        "position_observations": session.scalar(select(func.count(PositionObservation.id))) or 0,
        "cleaned_observations": session.scalar(select(func.count(CleanedObservation.id))) or 0,
        "track_segments": session.scalar(select(func.count(TrackSegment.id))) or 0,
        "track_points": session.scalar(select(func.count(TrackPoint.id))) or 0,
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


def latest_cleaned_observations(session: Session, limit: int) -> list[CleanedObservation]:
    """Return the most recent cleaned observations."""
    statement = select(CleanedObservation).order_by(desc(CleanedObservation.id)).limit(limit)

    return list(session.scalars(statement))


def top_aircraft(session: Session, limit: int) -> list[dict[str, int | str]]:
    """Return aircraft IDs with the largest number of observations."""
    statement = (
        select(CleanedObservation.aircraft_id, func.count(CleanedObservation.id))
        .group_by(CleanedObservation.aircraft_id)
        .order_by(desc(func.count(CleanedObservation.id)))
        .limit(limit)
    )

    return [
        {"aircraft_id": aircraft_id or "", "observations": count}
        for aircraft_id, count in session.execute(statement)
    ]


def beacon_counts(session: Session) -> list[dict[str, int | str]]:
    """Return cleaned observation counts grouped by beacon type."""
    statement = (
        select(CleanedObservation.beacon_type, func.count(CleanedObservation.id))
        .group_by(CleanedObservation.beacon_type)
        .order_by(desc(func.count(CleanedObservation.id)))
    )

    return [
        {"beacon_type": beacon_type or "unknown", "observations": count}
        for beacon_type, count in session.execute(statement)
    ]


def aircraft_type_counts(session: Session) -> list[dict[str, int | str | bool | None]]:
    """Return cleaned observation counts grouped by aircraft type."""
    statement = (
        select(
            CleanedObservation.aircraft_type,
            CleanedObservation.aircraft_type_name,
            func.count(CleanedObservation.id),
            func.count(func.distinct(CleanedObservation.aircraft_id)),
            CleanedObservation.is_likely_unconventional,
        )
        .group_by(
            CleanedObservation.aircraft_type,
            CleanedObservation.aircraft_type_name,
            CleanedObservation.is_likely_unconventional,
        )
        .order_by(desc(func.count(CleanedObservation.id)))
    )

    return [
        {
            "code": code,
            "label": label,
            "observations": observations,
            "aircraft": aircraft,
            "is_unconventional": bool(is_unconventional),
        }
        for code, label, observations, aircraft, is_unconventional in session.execute(statement)
    ]


def quality_summary(session: Session) -> dict[str, int | float | str | None]:
    """Return processed data quality and coverage summary."""
    total_positions = session.scalar(select(func.count(PositionObservation.id))) or 0
    swiss_positions = session.scalar(
        select(func.count(PositionObservation.id)).where(
            PositionObservation.latitude >= SWITZERLAND_BBOX["min_lat"],
            PositionObservation.latitude <= SWITZERLAND_BBOX["max_lat"],
            PositionObservation.longitude >= SWITZERLAND_BBOX["min_lon"],
            PositionObservation.longitude <= SWITZERLAND_BBOX["max_lon"],
        )
    ) or 0
    unique_aircraft = session.scalar(
        select(func.count(func.distinct(CleanedObservation.aircraft_id)))
    ) or 0
    unconventional_aircraft = session.scalar(
        select(func.count(func.distinct(CleanedObservation.aircraft_id))).where(
            CleanedObservation.is_likely_unconventional == 1
        )
    ) or 0
    good_segments = session.scalar(
        select(func.count(TrackSegment.id)).where(
            TrackSegment.n_points >= 20,
            TrackSegment.duration_s >= 120,
        )
    ) or 0
    time_row = session.execute(
        select(func.min(CleanedObservation.timestamp), func.max(CleanedObservation.timestamp))
    ).one()

    return {
        "total_positions": total_positions,
        "swiss_positions": swiss_positions,
        "outside_swiss_bbox_positions": total_positions - swiss_positions,
        "unique_aircraft": unique_aircraft,
        "unconventional_aircraft": unconventional_aircraft,
        "segments_with_20_points_120_s": good_segments,
        "first_timestamp": time_row[0],
        "last_timestamp": time_row[1],
    }


def top_segments(session: Session, limit: int) -> list[TrackSegment]:
    """Return the longest useful processed track segments."""
    statement = (
        select(TrackSegment)
        .where(TrackSegment.n_points >= 2)
        .order_by(desc(TrackSegment.n_points), desc(TrackSegment.duration_s))
        .limit(limit)
    )

    return list(session.scalars(statement))


def segment_points(session: Session, segment_id: int) -> list[dict[str, float | int | str | None]]:
    """Return trajectory points for one processed segment."""
    statement = (
        select(TrackPoint, CleanedObservation)
        .join(CleanedObservation, CleanedObservation.id == TrackPoint.cleaned_observation_id)
        .where(TrackPoint.segment_id == segment_id)
        .order_by(TrackPoint.sequence_index)
    )

    return [
        {
            "sequence_index": point.sequence_index,
            "timestamp": cleaned.timestamp,
            "latitude": cleaned.latitude,
            "longitude": cleaned.longitude,
            "altitude_m": cleaned.altitude_m,
            "ground_speed_kmh": point.estimated_ground_speed_kmh or cleaned.ground_speed_kmh,
            "track_deg": point.estimated_heading_deg or cleaned.track_deg,
            "climb_rate_ms": point.estimated_climb_rate_ms or cleaned.climb_rate_ms,
        }
        for point, cleaned in session.execute(statement)
    ]


def density_cells(
    session: Session,
    cell_size_deg: float,
    limit: int,
) -> list[dict[str, float | int | str | bool | None]]:
    """Return gridded cleaned-observation density cells."""
    lat_cell = (func.round(CleanedObservation.latitude / cell_size_deg) * cell_size_deg).label(
        "lat_cell"
    )
    lon_cell = (func.round(CleanedObservation.longitude / cell_size_deg) * cell_size_deg).label(
        "lon_cell"
    )
    observations = func.count(CleanedObservation.id).label("observations")
    statement = (
        select(
            lat_cell,
            lon_cell,
            CleanedObservation.aircraft_type,
            CleanedObservation.aircraft_type_name,
            CleanedObservation.is_likely_unconventional,
            observations,
            func.count(func.distinct(CleanedObservation.aircraft_id)),
            func.round(func.avg(CleanedObservation.altitude_m), 1),
            func.round(func.avg(CleanedObservation.ground_speed_kmh), 1),
        )
        .where(CleanedObservation.inside_swiss_bbox == 1)
        .group_by(
            lat_cell,
            lon_cell,
            CleanedObservation.aircraft_type,
            CleanedObservation.aircraft_type_name,
            CleanedObservation.is_likely_unconventional,
        )
        .order_by(desc(observations))
        .limit(limit)
    )

    return [
        {
            "lat": float(lat),
            "lon": float(lon),
            "aircraft_type": aircraft_type,
            "aircraft_type_name": aircraft_type_name,
            "unconventional": bool(is_unconventional),
            "observations": observations_count,
            "unique_aircraft": unique_aircraft,
            "avg_altitude_m": avg_altitude_m,
            "avg_speed_kmh": avg_speed_kmh,
        }
        for (
            lat,
            lon,
            aircraft_type,
            aircraft_type_name,
            is_unconventional,
            observations_count,
            unique_aircraft,
            avg_altitude_m,
            avg_speed_kmh,
        ) in session.execute(statement)
    ]


def coverage_gap_cells(
    session: Session,
    cell_size_deg: float,
    dropout_gap_seconds: float,
    max_gap_seconds: float,
    max_implied_speed_kmh: float,
    limit: int,
) -> list[dict[str, float | int | str]]:
    """Return geographic cells ranked by consecutive-observation dropout candidates."""
    lat_cell = (func.round(CleanedObservation.latitude / cell_size_deg) * cell_size_deg).label(
        "lat_cell"
    )
    lon_cell = (func.round(CleanedObservation.longitude / cell_size_deg) * cell_size_deg).label(
        "lon_cell"
    )
    transition_count = func.count(TrackPoint.id).label("transition_count")
    transition_statement = (
        select(lat_cell, lon_cell, transition_count)
        .join(CleanedObservation, CleanedObservation.id == TrackPoint.cleaned_observation_id)
        .where(CleanedObservation.inside_swiss_bbox == 1)
        .where(TrackPoint.dt_s.is_not(None))
        .where(TrackPoint.dt_s > 0)
        .where(TrackPoint.dt_s <= max_gap_seconds)
        .where(
            (TrackPoint.estimated_ground_speed_kmh.is_(None))
            | (TrackPoint.estimated_ground_speed_kmh <= max_implied_speed_kmh)
        )
        .group_by(lat_cell, lon_cell)
    )
    transitions = {
        (float(lat), float(lon)): count
        for lat, lon, count in session.execute(transition_statement)
    }
    dropout_count = func.count(TrackPoint.id).label("dropout_count")
    dropout_statement = (
        select(
            lat_cell,
            lon_cell,
            dropout_count,
            func.round(func.avg(TrackPoint.dt_s), 2),
            func.round(func.max(TrackPoint.dt_s), 2),
            func.count(func.distinct(CleanedObservation.aircraft_id)),
        )
        .join(CleanedObservation, CleanedObservation.id == TrackPoint.cleaned_observation_id)
        .where(CleanedObservation.inside_swiss_bbox == 1)
        .where(TrackPoint.dt_s >= dropout_gap_seconds)
        .where(TrackPoint.dt_s <= max_gap_seconds)
        .where(
            (TrackPoint.estimated_ground_speed_kmh.is_(None))
            | (TrackPoint.estimated_ground_speed_kmh <= max_implied_speed_kmh)
        )
        .group_by(lat_cell, lon_cell)
        .order_by(desc(dropout_count))
        .limit(limit)
    )

    return [
        {
            "lat": float(lat),
            "lon": float(lon),
            "transitions": transitions.get((float(lat), float(lon)), dropouts),
            "dropouts": dropouts,
            "dropout_rate": round(
                dropouts / transitions.get((float(lat), float(lon)), dropouts),
                6,
            ),
            "avg_gap_s": avg_gap_s,
            "max_gap_s": max_gap_s,
            "unique_aircraft": unique_aircraft,
            "altitude_band": "",
        }
        for lat, lon, dropouts, avg_gap_s, max_gap_s, unique_aircraft in session.execute(
            dropout_statement
        )
    ]
