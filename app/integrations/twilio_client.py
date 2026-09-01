from __future__ import annotations

import httpx

from app.core.config import settings

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class TwilioApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def send_sms(*, to: str, body: str) -> str:
    """Sends one text message via Twilio's Messages API.

    Prefers a Messaging Service (TWILIO_MESSAGING_SERVICE_SID) over a bare
    From number, since that's what enables RCS-with-SMS-fallback - if an RCS
    sender is attached to the Messaging Service in the Twilio Console,
    Twilio sends via RCS when the recipient's device supports it and falls
    back to SMS automatically, with no branching needed here.

    Returns the Twilio message SID on success.
    """
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured to send text messages."
        )

    if not (settings.twilio_messaging_service_sid or settings.twilio_from_number):
        raise RuntimeError(
            "Either TWILIO_MESSAGING_SERVICE_SID or TWILIO_FROM_NUMBER must be configured."
        )

    data = {"To": to, "Body": body}

    if settings.twilio_messaging_service_sid:
        data["MessagingServiceSid"] = settings.twilio_messaging_service_sid
    else:
        data["From"] = settings.twilio_from_number

    url = f"{TWILIO_API_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"

    with httpx.Client(
        timeout=30.0,
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
    ) as client:
        response = client.post(url, data=data)

    if response.status_code >= 400:
        try:
            payload = response.json()
            error_message = payload.get("message", response.text)
        except ValueError:
            error_message = response.text

        raise TwilioApiError(error_message)

    payload = response.json()
    message_sid = payload.get("sid")

    if not message_sid:
        raise TwilioApiError("Twilio response did not include a message sid.")

    return message_sid
