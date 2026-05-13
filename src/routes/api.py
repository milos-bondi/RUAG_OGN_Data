"""JSON API routes for OGN data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.envs import env
from src.db.session import get_session
from src.dtypes.ogn import CountsOut, ObservationOut
from src.db.functions.observations import beacon_counts, count_rows, latest_observations, top_aircraft


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


@router.get("/aircraft")
def aircraft(session: Session = Depends(get_session)) -> list[dict[str, int | str]]:
    """Return aircraft ranked by observation count."""
    return top_aircraft(session, 20)


@router.get("/beacons")
def beacons(session: Session = Depends(get_session)) -> list[dict[str, int | str]]:
    """Return beacon counts."""
    return beacon_counts(session)
