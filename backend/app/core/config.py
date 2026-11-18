"""Application configuration loaded strictly from environment variables (REQ-073)."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Centralized application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = Field(default="AI DJ Studio")
    APP_ENV: str = Field(default="development")
    APP_VERSION: str = Field(default="1.0.0")
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    LOG_LEVEL: str = Field(default="INFO")

    # CORS Origins (comma-separated string or list)
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
    )

    # Externalized infrastructure settings (populated via environment variables, never hardcoded secrets)
    DATABASE_URL: str | None = Field(default=None)
    JWT_SECRET_KEY: str | None = Field(default=None)
    JWT_REFRESH_SECRET_KEY: str | None = Field(default=None)
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_SECONDS: int = Field(default=3600, ge=60, le=3600)
    REFRESH_TOKEN_EXPIRE_SECONDS: int = Field(default=604800, ge=3600)
    LLM_PROVIDER: str = Field(default="deterministic_fallback")
    LLM_API_KEY: str | None = Field(default=None)

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse comma-separated CORS_ORIGINS into a clean list of origin URLs."""
        if not self.CORS_ORIGINS:
            return []
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
