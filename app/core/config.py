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

    # Existing ScanX production database.
    #
    # Read-only by default. The small set of explicitly-approved writes is
    # isolated in app/repositories/production_writes.py.
    #
    # Command Center never runs Alembic migrations against this schema.
    prod_database_url: str | None = None

    dashboard_poll_interval_seconds: int = 5

    # No default: missing JWT secret must fail startup.
    jwt_secret_key: Annotated[str, Field(min_length=32)]
    jwt_algorithm: str = "HS256"

    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    cors_allowed_origins: str = (
        "http://localhost:5173,"
        "http://localhost:3000,"
        "https://scanx-command-center-fe-794794356928.us-central1.run.app"
    )

    frontend_base_url: str = "http://localhost:3000"

    fax_email_subject: str = "ScanX Command Center"
    fax_feedback_email: str = "scheduling@scanx.care"

    gcs_reports_bucket: str = "scanx-reports"

    pdf_proxy_base_url: str = "https://scanx-pdf-proxy-794794356928.us-central1.run.app"

    gmail_sender_email: str | None = None
    gmail_service_account_json: str | None = None

    # Twilio
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_messaging_service_sid: str | None = None
    twilio_from_number: str | None = None

    # Payment-reminder Content Template triggered after successful check-in.
    #
    # Content variables:
    #   1 -> patient first name
    #   2 -> payment link
    twilio_payment_template_sid: str | None = None

    # Acuity Scheduling
    acuity_user_id: str | None = None
    acuity_api_key: str | None = None

    # Confirmed "Checked In" Acuity label.
    acuity_checked_in_label_id: int = 21644010

    # Google Chat
    #
    # Never put actual webhook URLs into source control.
    gchat_webhook_dallas: str | None = None
    gchat_webhook_fairview: str | None = None
    gchat_webhook_fallback: str | None = None

    jotform_api_key: str | None = None

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

    @field_validator("jwt_access_token_expire_minutes")
    @classmethod
    def validate_access_token_expiry(cls, value: int) -> int:
        if not 0 < value <= 120:
            raise ValueError("jwt_access_token_expire_minutes must be between 1 and 120")

        return value

    @field_validator("jwt_refresh_token_expire_days")
    @classmethod
    def validate_refresh_token_expiry(cls, value: int) -> int:
        if not 0 < value <= 30:
            raise ValueError("jwt_refresh_token_expire_days must be between 1 and 30")

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
