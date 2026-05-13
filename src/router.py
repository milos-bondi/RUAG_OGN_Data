"""Router assembly and API wiring."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.envs import env
from src.db.session import create_tables
from src.cron.collector import CollectorService
from src.routes.api import router as api_router
from src.routes.health import router as health_router
from src.routes.dashboard import router as dashboard_router


collector = CollectorService()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start database tables and collector for the API process lifetime."""
    create_tables()

    # The single `uv run fastapi` command starts both the API and the collector.
    if env.collect_on_startup:
        collector.start()

    yield

    if env.collect_on_startup:
        collector.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title=env.app_name, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="src/static"), name="static")
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
