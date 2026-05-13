"""Health check routes."""

from fastapi import APIRouter

from src.envs import env
from src.dtypes.ogn import HealthOut


router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Return application health."""
    return HealthOut(status="ok", database_url=env.database_url)
