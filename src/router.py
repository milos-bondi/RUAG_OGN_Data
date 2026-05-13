"""Router assembly and API wiring."""

from fastapi import FastAPI

from src.envs import env
from src.db.session import create_tables
from src.routes.api import router as api_router
from src.routes.health import router as health_router
from src.routes.dashboard import router as dashboard_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    create_tables()
    app = FastAPI(title=env.app_name)
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
