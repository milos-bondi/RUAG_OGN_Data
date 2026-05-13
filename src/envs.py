"""Environment variable loading for the OGN application."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file="src/.env", env_prefix="OGN_")

    app_name: str = "RUAG OGN Dashboard"
    database_url: str = "sqlite:///data/live/ogn_live.db"
    aprs_user: str = "N0CALL"
    aprs_filter: str = "r/46.8/8.2/250"
    aprs_server_host: str = "aprs.glidernet.org"
    commit_every: int = 100
    sqlite_timeout_seconds: float = 120.0
    collection_profile: str = "all"
    max_altitude_m: float = 6000.0
    max_speed_kmh: float = 300.0
    api_observation_limit: int = 500
    collect_on_startup: bool = True
    process_on_startup: bool = True
    processor_interval_seconds: float = 15.0
    processor_batch_size: int = 5000
    segment_gap_seconds: float = 60.0
    max_jump_speed_kmh: float = 1200.0
    include_outside_swiss: bool = False
    include_receivers: bool = False
    include_static: bool = False
    host: str = "127.0.0.1"
    port: int = 8000


env = Settings()
