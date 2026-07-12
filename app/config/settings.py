from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_timezone: str = "Europe/Moscow"
    database_url: str = "postgresql+asyncpg://procurement:procurement@localhost:5432/procurement"

    google_application_credentials: Path = Field(
        default=Path("/run/secrets/google-service-account.json"),
        validation_alias="GOOGLE_APPLICATION_CREDENTIALS",
    )
    google_spreadsheet_id: str = ""
    google_main_sheet: str = "2026 (44)"
    google_review_sheet: str = "Требуется проверка"

    document_cache_dir: Path = Path("document-cache")
    eis_base_url: str = "https://zakupki.gov.ru"
    eis_min_request_interval_seconds: float = 1.2
    eis_verify_ssl: bool = True
    eis_ca_bundle: Path | None = None
    eis_proxy_url: str | None = None
    scheduler_interval_minutes: int = 30
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    web_ui_enabled: bool = True
    web_ui_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
