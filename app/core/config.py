from functools import lru_cache
from typing import Annotated
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "scanx-command-center-api"
    app_env: str = "local"
    debug: bool = False

    auth_max_failed_login_attempts: int = 3
    auth_account_lock_minutes: int = 5
    password_reset_token_expire_minutes: int = 30

    database_url: str | None = None
    database_host: str | None = None
    database_name: str = "scanx_app"
    database_user: str = "postgres"
    database_password: str | None = None

    # Read-only connection to the existing production ScanX database
    # (appointment/clinics/patients, etc.). Command Center never writes to
    # this database and never runs migrations against it.
    prod_database_url: str | None = None
    dashboard_poll_interval_seconds: int = 5

    jwt_secret_key: Annotated[str, Field(min_length=32)] = (
        "change-me-for-local-development-only-123456"
    )
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000,https://scanx-command-center-fe-794794356928.us-central1.run.app"

    # Fax is sent via WestFax's email-to-fax gateway (an email to
    # {digits}@westfax.com), not their REST API - so it reuses the Gmail
    # sending config below. fax_email_subject is a fallback subject line,
    # used only if the patient's appointment can't be looked up (normally
    # the subject is built from name + exam type). fax_feedback_email is
    # CC'd on every fax email as a single, easily-changed receipt address.
    fax_email_subject: str = "ScanX Command Center"
    fax_feedback_email: str = "scheduling@scanx.care"

    gcs_reports_bucket: str = "scanx-reports"

    gmail_sender_email: str | None = None
    gmail_service_account_json: str | None = None

    # Text messages (SMS/MMS/RCS) are sent via Twilio's Messages API.
    # twilio_messaging_service_sid is preferred over twilio_from_number: a
    # Messaging Service can have an RCS sender attached in the Twilio
    # Console, in which case Twilio automatically sends via RCS when the
    # recipient's device supports it and falls back to plain SMS otherwise -
    # no extra code needed on our side for that behavior.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_messaging_service_sid: str | None = None
    twilio_from_number: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: bool | str) -> bool:
        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {"release", "prod", "production"}:
                return False

            if normalized in {"dev", "development", "local"}:
                return True

        return value

    @field_validator(
        "jwt_access_token_expire_minutes",
        "jwt_refresh_token_expire_days",
    )
    @classmethod
    def validate_positive_expiry(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("JWT expiry values must be greater than zero")
        return value

    @property
    def sqlalchemy_database_uri(self) -> str:
        if self.database_url:
            return self.database_url

        if not self.database_password:
            raise ValueError("DATABASE_URL or DATABASE_PASSWORD must be configured")

        host = self.database_host or "localhost"
        password = quote_plus(self.database_password)

        return (
            f"postgresql+psycopg://{self.database_user}:{password}"
            f"@/{self.database_name}?host={host}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
