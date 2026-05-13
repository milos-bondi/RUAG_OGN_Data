"""SQLAlchemy engine and session setup."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from src.envs import env
from src.db.models.ogn import Base


def ensure_sqlite_directory() -> None:
    """Create the configured SQLite parent directory when needed."""
    url = make_url(env.database_url)
    if not url.drivername.startswith("sqlite"):
        return

    if url.database and url.database not in {":memory:"}:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


ensure_sqlite_directory()
engine = create_engine(
    env.database_url,
    connect_args={"check_same_thread": False, "timeout": env.sqlite_timeout_seconds},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def configure_sqlite(connection, _connection_record) -> None:
    """Configure SQLite connections for concurrent collector/API access."""
    cursor = connection.cursor()

    # WAL mode allows the API to read while the collector writes.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={int(env.sqlite_timeout_seconds * 1000)}")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_tables() -> None:
    """Create database tables when they do not already exist."""
    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """Yield a database session for FastAPI dependency injection."""
    session = SessionLocal()

    try:
        yield session
    finally:
        session.close()
