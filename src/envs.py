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
    processor_batch_size: int = 50000
    segment_gap_seconds: float = 60.0
    max_jump_speed_kmh: float = 1200.0
    dashboard_refresh_seconds: int = 30
    dashboard_window_hours: float = 24.0
    dashboard_grid_degrees: float = 0.05
    dashboard_dropout_limit: int = 3000
    dashboard_dropout_min_distance_km: float = 0.2
    dashboard_dropout_max_gap_seconds: float = 600.0
    dashboard_dropout_max_implied_speed_kmh: float = 300.0
    dashboard_dropout_hotspot_min_transitions: int = 30
    dashboard_include_all_dropout_aircraft: bool = False
    dashboard_stability_window_days: int = 3
    dashboard_stability_history_windows: int = 6
    dashboard_stability_min_points: int = 1000
    dashboard_stability_min_segments: int = 100
    include_outside_swiss: bool = False
    include_receivers: bool = False
    include_static: bool = False
    host: str = "127.0.0.1"
    port: int = 8000


env = Settings()
