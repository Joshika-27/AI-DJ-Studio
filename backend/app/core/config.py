"""Application configuration loaded strictly from environment variables (REQ-073, F-04)."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

ALLOWED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
KNOWN_PLACEHOLDER_SECRETS = {
    "replace_with_a_strong_32_byte_secret_key",
    "replace_with_a_strong_32_byte_refresh_secret_key",
    "replace_with_32_byte_random_secret",
    "changeme",
    "secret",
    "password",
    "default",
    "your_secret_key",
}
MIN_JWT_SECRET_LENGTH = 32


def _validate_jwt_secret_strength(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} cannot be an empty string.")
    lower_val = stripped.lower()
    if (
        lower_val in KNOWN_PLACEHOLDER_SECRETS
        or lower_val.startswith("replace_with_")
        or lower_val.startswith("example_")
    ):
        raise ValueError(
            f"{field_name} uses a forbidden placeholder/example value."
        )
    if len(stripped) < MIN_JWT_SECRET_LENGTH:
        raise ValueError(
            f"{field_name} must be at least {MIN_JWT_SECRET_LENGTH} characters long."
        )
    return stripped


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

    @field_validator("JWT_ALGORITHM")
    @classmethod
    def validate_jwt_algorithm(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in ALLOWED_JWT_ALGORITHMS:
            raise ValueError(
                f"JWT_ALGORITHM must be one of {sorted(ALLOWED_JWT_ALGORITHMS)}."
            )
        return normalized

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_access_secret(cls, value: str | None) -> str | None:
        return _validate_jwt_secret_strength(value, "JWT_SECRET_KEY")

    @field_validator("JWT_REFRESH_SECRET_KEY")
    @classmethod
    def validate_refresh_secret(cls, value: str | None) -> str | None:
        return _validate_jwt_secret_strength(value, "JWT_REFRESH_SECRET_KEY")

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if (
            self.JWT_SECRET_KEY is not None
            and self.JWT_REFRESH_SECRET_KEY is not None
            and self.JWT_SECRET_KEY == self.JWT_REFRESH_SECRET_KEY
        ):
            raise ValueError(
                "JWT_SECRET_KEY and JWT_REFRESH_SECRET_KEY must be distinct secrets."
            )

        if self.APP_ENV.strip().lower() == "production":
            if not self.JWT_SECRET_KEY:
                raise ValueError(
                    "JWT_SECRET_KEY environment variable must be configured in production."
                )
            if not self.JWT_REFRESH_SECRET_KEY:
                raise ValueError(
                    "JWT_REFRESH_SECRET_KEY environment variable must be configured in production."
                )
        return self

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
