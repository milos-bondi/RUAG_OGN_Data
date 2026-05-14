"""Router assembly and API wiring."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.envs import env
from src.db.session import create_tables
from src.cron.collector import CollectorService
from src.cron.processor import ProcessorService
from src.routes.api import router as api_router
from src.routes.health import router as health_router
from src.routes.dashboard import router as dashboard_router
from src.routes.dashboard import dashboard_cache


collector = CollectorService()
processor = ProcessorService()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start database tables and collector for the API process lifetime."""
    create_tables()

    # The single `uv run fastapi` command starts both the API and the collector.
    dashboard_cache.start()
    if env.collect_on_startup:
        collector.start()
    if env.process_on_startup:
        processor.start()

    yield

    if env.process_on_startup:
        processor.stop()
    if env.collect_on_startup:
        collector.stop()
    dashboard_cache.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title=env.app_name, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
