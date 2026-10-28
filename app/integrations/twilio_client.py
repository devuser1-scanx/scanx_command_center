from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import NamedTuple

import httpx
import jwt

from app.core.config import settings

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class TwilioApiError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Twilio's numeric error code when it sent one (e.g. 21610: the
        # recipient replied STOP). None for network or parsing failures.
        self.code = code


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


def _post_resource(
    resource: str,
    data: Mapping[str, str | list[str]],
) -> dict:
    """POSTs a form to a Twilio account resource (Messages, Calls) and returns
    the JSON body. Raises TwilioApiError with Twilio's own error code."""
    account_sid, auth_token = _require_twilio_credentials()

    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/{resource}.json"

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
        error_code: int | None = None

        try:
            payload = response.json()
            error_message = payload.get(
                "message",
                response.text,
            )
            raw_code = payload.get("code")
            error_code = raw_code if isinstance(raw_code, int) else None
        except ValueError:
            error_message = response.text

        raise TwilioApiError(str(error_message), code=error_code)

    try:
        return response.json()
    except ValueError as exc:
        raise TwilioApiError("Twilio returned an invalid JSON response.") from exc


def _send_message(
    data: dict[str, str],
) -> TwilioSendResult:
    payload = _post_resource("Messages", data)

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
    status_callback: str | None = None,
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

    if status_callback:
        # Twilio posts delivery updates (sent / delivered / read / failed) here.
        data["StatusCallback"] = status_callback

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


def voice_token_configured(twiml_app_sid: str | None = None) -> bool:
    """Whether browser calling can be set up. `twiml_app_sid` names the TwiML App
    to check (the Calls module has its own); by default it is the Fax Portal's."""
    return bool(
        settings.twilio_account_sid
        and settings.twilio_api_key_sid
        and settings.twilio_api_key_secret
        and (twiml_app_sid or settings.twilio_twiml_app_sid)
    )


def build_voice_access_token(
    *,
    identity: str,
    ttl_seconds: int = 600,
    twiml_app_sid: str | None = None,
) -> str:
    """A short-lived Twilio Access Token (JWT signed with the API key's secret)
    that lets one browser place outgoing calls through a TwiML App (the Fax
    Portal's unless `twiml_app_sid` names another). The token only starts a
    call; the patient's number is never in it."""
    app_sid = twiml_app_sid or settings.twilio_twiml_app_sid

    if not voice_token_configured(app_sid):
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID, TWILIO_API_KEY_SID, TWILIO_API_KEY_SECRET and "
            "a TwiML App SID must be configured for browser calling."
        )

    now = int(time.time())

    return jwt.encode(
        {
            "jti": f"{settings.twilio_api_key_sid}-{now}",
            "iss": settings.twilio_api_key_sid,
            "sub": settings.twilio_account_sid,
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "grants": {
                "identity": identity,
                "voice": {"outgoing": {"application_sid": app_sid}},
            },
        },
        settings.twilio_api_key_secret or "",
        algorithm="HS256",
        headers={"cty": "twilio-fpa;v=1"},
    )


def validate_twilio_signature(
    *,
    url: str,
    params: Mapping[str, str],
    signature: str | None,
) -> bool:
    """Checks the X-Twilio-Signature header of a webhook request.

    Twilio signs the full request URL followed by every POST parameter
    (name then value, sorted by name) with HMAC-SHA1 using the account's auth
    token. Returns False if there is no signature or no auth token configured.
    """
    if not signature or not settings.twilio_auth_token:
        return False

    signed = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        settings.twilio_auth_token.encode(),
        signed.encode(),
        hashlib.sha1,
    ).digest()

    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
