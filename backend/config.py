"""Runtime configuration for the booking backend and the maintenance controller.

Every value can be overridden with an environment variable of the same name in
upper case (``DATABASE_URL``, ``ADMIN_TOKEN``, ...).  Nothing that differs
between a laptop and the cloud is hard-coded in the source code, as required by
the assignment brief (section 5.2).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings shared by the backend, the frontend helpers and the controller."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application ---
    app_name: str = "Room Booking Service"
    app_version: str = "1.0.0"
    database_url: str = "sqlite:///./data/app.db"
    admin_token: str = "local-dev-admin-token"
    timezone: str = "Europe/Budapest"
    open_hour: int = 8
    close_hour: int = 20
    log_level: str = "INFO"
    log_file: str = "logs/app.log"
    cors_allow_origins: str = "*"
    auto_create_schema: bool = True

    # --- maintenance controller (local process only) ---
    neon_api_key: str = ""
    neon_project_id: str = ""
    neon_project_name: str = "room-booking-prod"
    neon_region_id: str = "aws-eu-central-1"
    neon_pg_version: int = 17
    render_api_key: str = ""
    render_service_id: str = ""
    backend_base_url: str = "http://127.0.0.1:8000"
    maintenance_interval_minutes: int = 60
    maintenance_expiry_warning_hours: int = 72
    maintenance_auto_replace: bool = False
    maintenance_backup_dir: str = "backups"
    maintenance_state_dir: str = "maintenance_state"
    maintenance_log_file: str = "logs/maintenance.log"
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 3

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins as a list (``*`` is kept as a single entry)."""
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object (cached)."""
    return Settings()


def reload_settings() -> Settings:
    """Clear the cache and re-read the environment (used by the start script)."""
    get_settings.cache_clear()
    return get_settings()
