"""HTML dashboard route."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()
STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"


@router.get("/", response_class=FileResponse)
def dashboard() -> FileResponse:
    """Return the browser dashboard page."""
    return FileResponse(STATIC_ROOT / "index.html")
