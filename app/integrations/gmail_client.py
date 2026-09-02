from __future__ import annotations

import base64
import json
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.core.config import settings

# Reused from westfax_client.py - both are the same "filename + bytes +
# content-type" shape used for a downloaded GCS report blob.
from app.integrations.westfax_client import FaxAttachment

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

GMAIL_SENDER_DISPLAY_NAME = "ScanX Health LLC"


class GmailApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _get_access_token() -> str:
    """Gets a short-lived access token for GMAIL_SENDER_EMAIL, via Workspace
    domain-wide delegation. Requires the service account's Client ID to be
    authorized in Workspace Admin Console for the gmail.send scope.
    """
    if not (settings.gmail_sender_email and settings.gmail_service_account_json):
        raise RuntimeError(
            "GMAIL_SENDER_EMAIL and GMAIL_SERVICE_ACCOUNT_JSON must be configured to send email."
        )

    try:
        info = json.loads(settings.gmail_service_account_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GMAIL_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc

    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=[GMAIL_SEND_SCOPE],
    ).with_subject(settings.gmail_sender_email)

    credentials.refresh(Request())

    if not credentials.token:
        raise GmailApiError("Could not obtain a Gmail access token.")

    return credentials.token


def send_email(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    html_body: str,
    attachments: list[FaxAttachment],
) -> str:
    """Sends one email via the Gmail API, from GMAIL_SENDER_EMAIL.

    Returns the Gmail message id on success.
    """
    if not to:
        raise ValueError("At least one recipient is required.")

    message = MIMEMultipart("mixed")
    message["From"] = formataddr((GMAIL_SENDER_DISPLAY_NAME, settings.gmail_sender_email))
    message["To"] = ", ".join(to)

    if cc:
        message["Cc"] = ", ".join(cc)

    if bcc:
        message["Bcc"] = ", ".join(bcc)

    message["Subject"] = subject

    message.attach(MIMEText(html_body, "html"))

    for attachment in attachments:
        part = MIMEApplication(attachment.content)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=attachment.filename,
        )
        message.attach(part)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    access_token = _get_access_token()

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
        )

    if response.status_code >= 400:
        try:
            payload = response.json()
            error_message = payload.get("error", {}).get("message", response.text)
        except ValueError:
            error_message = response.text

        raise GmailApiError(error_message)

    payload = response.json()
    message_id = payload.get("id")

    if not message_id:
        raise GmailApiError("Gmail response did not include a message id.")

    return message_id
