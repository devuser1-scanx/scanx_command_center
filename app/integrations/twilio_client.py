from __future__ import annotations

import json
from typing import NamedTuple

import httpx

from app.core.config import settings

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class TwilioApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TwilioSendResult(NamedTuple):
    message_sid: str
    from_number: str | None


def _require_twilio_credentials() -> tuple[str, str]:
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured to send text messages."
        )

    return (
        settings.twilio_account_sid,
        settings.twilio_auth_token,
    )


def _send_message(
    data: dict[str, str],
) -> TwilioSendResult:
    account_sid, auth_token = _require_twilio_credentials()

    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"

    try:
        with httpx.Client(
            timeout=30.0,
            auth=(account_sid, auth_token),
        ) as client:
            response = client.post(
                url,
                data=data,
            )

    except httpx.HTTPError as exc:
        raise TwilioApiError(f"Could not reach Twilio: {exc}") from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            error_message = payload.get(
                "message",
                response.text,
            )
        except ValueError:
            error_message = response.text

        raise TwilioApiError(str(error_message))

    try:
        payload = response.json()
    except ValueError as exc:
        raise TwilioApiError("Twilio returned an invalid JSON response.") from exc

    message_sid = payload.get("sid")

    if not message_sid:
        raise TwilioApiError("Twilio response did not include a message sid.")

    return TwilioSendResult(
        message_sid=str(message_sid),
        from_number=payload.get("from"),
    )


def send_sms(
    *,
    to: str,
    body: str,
) -> TwilioSendResult:
    """
    Send a normal SMS/RCS-capable message.

    Messaging Service is preferred because it can use an RCS sender and
    automatically fall back to SMS.
    """
    if not (settings.twilio_messaging_service_sid or settings.twilio_from_number):
        raise RuntimeError(
            "Either TWILIO_MESSAGING_SERVICE_SID or TWILIO_FROM_NUMBER must be configured."
        )

    data = {
        "To": to,
        "Body": body,
    }

    if settings.twilio_messaging_service_sid:
        data["MessagingServiceSid"] = settings.twilio_messaging_service_sid
    else:
        data["From"] = settings.twilio_from_number or ""

    return _send_message(data)


def send_content_message(
    *,
    to: str,
    content_sid: str,
    content_variables: dict[str, str],
) -> TwilioSendResult:
    """
    Send a Twilio Content Template through the configured Messaging Service.

    Used by Manual Check-in to preserve the existing payment-reminder
    RCS behavior.
    """
    if not settings.twilio_messaging_service_sid:
        raise RuntimeError(
            "TWILIO_MESSAGING_SERVICE_SID must be configured "
            "to send Twilio Content Template messages."
        )

    data = {
        "To": to,
        "MessagingServiceSid": (settings.twilio_messaging_service_sid),
        "ContentSid": content_sid,
        "ContentVariables": json.dumps(content_variables),
    }

    return _send_message(data)
