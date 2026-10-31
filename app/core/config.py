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

    # Automatic logoff (HIPAA 45 CFR 164.312(a)(2)(iii)). The browser signs the
    # user out after this much inactivity (real input, not background polling),
    # warning them `session_warning_seconds` before. A session also ends this many
    # hours after login whatever the activity, so it cannot slide on forever.
    session_idle_timeout_minutes: int = 45
    session_warning_seconds: int = 120
    session_max_hours: int = 12

    cors_allowed_origins: str = (
        "http://localhost:5173,"
        "http://localhost:3000,"
        "https://scanx-command-center-fe-794794356928.us-central1.run.app,"
        "https://scanx-command-center.cloud.run"
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
    # Voice-capable Twilio number the Fax Portal's click-to-call dials from (the
    # caller ID the patient sees). An RCS sender cannot place calls.
    twilio_voice_from_number: str | None = None
    # Browser calling (Twilio Voice SDK): the staff member talks through the
    # browser, and Twilio dials the patient from twilio_voice_from_number.
    # An API key (Twilio Console > Account > API keys) signs the short-lived
    # token the browser connects with; the TwiML App's Voice URL is
    # <PUBLIC_API_BASE_URL>/fax-portal/webhooks/twilio/voice/client and its
    # Status Callback URL <PUBLIC_API_BASE_URL>/fax-portal/webhooks/twilio/voice/status.
    twilio_api_key_sid: str | None = None
    twilio_api_key_secret: str | None = None
    twilio_twiml_app_sid: str | None = None

    # Calls module (the Web Dialer). Its browser tokens name the standalone Web
    # Dialer's own TwiML App (kept apart from the Fax Portal's, above), so Twilio
    # asks the standalone service what to do with each call: that service dials,
    # records, logs the call in the production call_logs / dialer_call_* tables and
    # sends the missed-call alerts, exactly as it does for its own frontend.
    # Command Center adds its own record of who placed each call and reads the
    # shared tables. Unset: Calls answers 503.
    dialer_twiml_app_sid: str | None = None

    # Messages module (the Patient Inbox). It sends through the same Twilio
    # account and Messaging Service as the rest of the app (twilio_account_sid,
    # twilio_auth_token, twilio_messaging_service_sid) and stores
    # twilio_voice_from_number as the business number of each conversation.
    # twilio_rcs_card_content_sid is the Content Template of the exam RCS card;
    # an exam with its own template (twilio_rcs_card_content_sid_<exam>) uses
    # that one instead.
    # Incoming texts keep arriving through the standalone Inbox service (its
    # Twilio webhook), which writes them to Firestore; Command Center reads and
    # writes the same Firestore conversations.
    twilio_rcs_card_content_sid: str | None = None
    twilio_rcs_card_content_sid_fibroscan: str | None = None
    twilio_rcs_card_content_sid_thyroid: str | None = None
    twilio_rcs_card_content_sid_abdominal: str | None = None
    twilio_rcs_card_content_sid_transvaginal: str | None = None
    # Cloud Run service that returns a short-lived signed URL for an image in the
    # card images bucket (GET ?file=<path>); the cards' pictures come from it.
    signed_url_service_url: str = "https://generatesignedurl-794794356928.us-central1.run.app"

    # Payment-reminder Content Template triggered after successful check-in.
    #
    # Content variables:
    #   1 -> patient first name
    #   2 -> payment link
    twilio_payment_template_sid: str | None = None

    # Public base URL of this API (e.g. https://<service>.run.app), used to tell
    # Twilio where to post message status callbacks and call instructions, and
    # to verify their signatures. Unset: texts are still sent, just without
    # delivery updates, and click-to-call is unavailable.
    public_api_base_url: str | None = None

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
    gchat_webhook_arlington: str | None = None
    gchat_webhook_fallback: str | None = None

    # Google Chat card buttons.
    #
    # n8n webhooks behind "Admit patient" / "Ask to wait".
    n8n_webhook_base_url: str = "https://n8n-794794356928.us-central1.run.app/webhook"

    jotform_api_key: str | None = None

    # Fax Portal: WestFax Comprehend webhook.
    #
    # The webhook is public, so it fails closed: with no token configured
    # every delivery is rejected. Accepted as "Authorization: Bearer <token>",
    # "X-Api-Key: <token>" or "?token=" (prefer a header - query strings end
    # up in request logs). Both values belong in Secret Manager.
    comprehend_webhook_token: str | None = None
    # Optional: the connector's HMAC-SHA256 secret. When set, a valid
    # signature header is required in addition to the token.
    comprehend_webhook_hmac_secret: str | None = None
    comprehend_webhook_max_body_bytes: int = 10 * 1024 * 1024

    # Private bucket the Comprehend GCS connector writes the fax PDFs to. Never
    # public: PDFs reach the browser only through an authenticated backend
    # endpoint. The service account running the API needs read access to it.
    # The connector's file path template must start with {JobId}/ (see
    # app/integrations/gcs_faxes.py).
    comprehend_gcs_bucket: str = "westfax_comprehend_fax_pdfs"

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

    @field_validator("session_idle_timeout_minutes")
    @classmethod
    def validate_session_idle_timeout(cls, value: int) -> int:
        if not 1 <= value <= 120:
            raise ValueError("session_idle_timeout_minutes must be between 1 and 120")

        return value

    @field_validator("session_warning_seconds")
    @classmethod
    def validate_session_warning(cls, value: int) -> int:
        if not 10 <= value <= 600:
            raise ValueError("session_warning_seconds must be between 10 and 600")

        return value

    @field_validator("session_max_hours")
    @classmethod
    def validate_session_max_hours(cls, value: int) -> int:
        if not 1 <= value <= 24:
            raise ValueError("session_max_hours must be between 1 and 24")

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
