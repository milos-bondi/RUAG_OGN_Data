"""JSON API routes for OGN data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.envs import env
from src.db.session import get_session
from src.dtypes.ogn import CleanedObservationOut, CountsOut, ObservationOut, TrackSegmentOut
from src.db.functions.observations import aircraft_type_counts, beacon_counts, count_rows
from src.db.functions.observations import coverage_gap_cells, density_cells
from src.db.functions.observations import latest_cleaned_observations
from src.db.functions.observations import latest_observations, quality_summary, segment_points
from src.db.functions.observations import top_aircraft, top_segments


router = APIRouter(prefix="/api")


@router.get("/counts", response_model=CountsOut)
def counts(session: Session = Depends(get_session)) -> CountsOut:
    """Return high-level database counts."""
    return CountsOut(**count_rows(session))


@router.get("/observations", response_model=list[ObservationOut])
def observations(
    limit: int = Query(default=100, ge=1, le=env.api_observation_limit),
    session: Session = Depends(get_session),
) -> list[ObservationOut]:
    """Return latest parsed observations."""
    return [ObservationOut.model_validate(row) for row in latest_observations(session, limit)]


@router.get("/cleaned-observations", response_model=list[CleanedObservationOut])
def cleaned_observations(
    limit: int = Query(default=100, ge=1, le=env.api_observation_limit),
    session: Session = Depends(get_session),
) -> list[CleanedObservationOut]:
    """Return latest cleaned observations."""
    return [
        CleanedObservationOut.model_validate(row)
        for row in latest_cleaned_observations(session, limit)
    ]


@router.get("/aircraft")
def aircraft(session: Session = Depends(get_session)) -> list[dict[str, int | str]]:
    """Return aircraft ranked by observation count."""
    return top_aircraft(session, 20)


@router.get("/beacons")
def beacons(session: Session = Depends(get_session)) -> list[dict[str, int | str]]:
    """Return beacon counts."""
    return beacon_counts(session)


@router.get("/aircraft-types")
def aircraft_types(
    session: Session = Depends(get_session),
) -> list[dict[str, int | str | bool | None]]:
    """Return aircraft type counts."""
    return aircraft_type_counts(session)


@router.get("/quality")
def quality(session: Session = Depends(get_session)) -> dict[str, int | float | str | None]:
    """Return processed quality and coverage summary."""
    return quality_summary(session)


@router.get("/density")
def density(
    cell_size_deg: float = Query(default=0.1, gt=0, le=1),
    limit: int = Query(default=500, ge=1, le=2000),
    session: Session = Depends(get_session),
) -> list[dict[str, float | int | str | bool | None]]:
    """Return geographic density cells for the dashboard."""
    return density_cells(session, cell_size_deg, limit)


@router.get("/coverage-gaps")
def coverage_gaps(
    cell_size_deg: float = Query(default=0.1, gt=0, le=1),
    dropout_gap_seconds: float = Query(default=60.0, gt=0),
    max_gap_seconds: float = Query(default=600.0, gt=0),
    max_implied_speed_kmh: float = Query(default=1200.0, gt=0),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[dict[str, float | int | str]]:
    """Return coverage dropout proxy cells."""
    return coverage_gap_cells(
        session,
        cell_size_deg,
        dropout_gap_seconds,
        max_gap_seconds,
        max_implied_speed_kmh,
        limit,
    )


@router.get("/segments", response_model=list[TrackSegmentOut])
def segments(
    limit: int = Query(default=25, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[TrackSegmentOut]:
    """Return top processed track segments."""
    return [TrackSegmentOut.model_validate(row) for row in top_segments(session, limit)]


@router.get("/segments/{segment_id}/points")
def track_points(
    segment_id: int,
    session: Session = Depends(get_session),
) -> list[dict[str, float | int | str | None]]:
    """Return points for one processed segment."""
    return segment_points(session, segment_id)
