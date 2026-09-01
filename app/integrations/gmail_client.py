from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from pathlib import Path

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.core.config import settings

# Reused from westfax_client.py - both are the same "filename + bytes +
# content-type" shape used for a downloaded GCS report blob.
from app.integrations.westfax_client import FaxAttachment

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


@dataclass(frozen=True)
class InlineImage:
    """An image embedded in the HTML body via <img src="cid:{content_id}">,
    as opposed to a regular file attachment.
    """

    content_id: str
    content: bytes
    content_type: str


# The frontend's mail body template references this exact content_id via
# <img src="cid:scanx-logo">, so this string must match
# send-mail-dialog.tsx's SCANX_LOGO_CONTENT_ID.
SCANX_LOGO_CONTENT_ID = "scanx-logo"

# app/integrations/gmail_client.py -> parents[2] is the project root, where
# assets/ lives (a sibling of app/, migrations/, requirements.txt, etc).
_SCANX_LOGO_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "ScanX animated logo.gif"
)


@lru_cache
def get_scanx_logo_inline_image() -> InlineImage | None:
    """Loads the ScanX animated logo for embedding in email signatures.

    Returns None (rather than raising) if the asset is missing, so a
    missing logo degrades to "no logo" instead of breaking mail sending.
    """
    if not _SCANX_LOGO_PATH.exists():
        return None

    return InlineImage(
        content_id=SCANX_LOGO_CONTENT_ID,
        content=_SCANX_LOGO_PATH.read_bytes(),
        content_type="image/gif",
    )


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
    inline_images: list[InlineImage] | None = None,
) -> str:
    """Sends one email via the Gmail API, from GMAIL_SENDER_EMAIL.

    Returns the Gmail message id on success.
    """
    if not to:
        raise ValueError("At least one recipient is required.")

    message = MIMEMultipart("mixed")
    message["From"] = settings.gmail_sender_email
    message["To"] = ", ".join(to)

    if cc:
        message["Cc"] = ", ".join(cc)

    if bcc:
        message["Bcc"] = ", ".join(bcc)

    message["Subject"] = subject

    # The body + any inline (cid-referenced) images live in a
    # multipart/related part, nested inside the outer multipart/mixed
    # alongside regular file attachments - the standard MIME structure for
    # HTML email with embedded images, for maximum client compatibility.
    body_related = MIMEMultipart("related")
    body_related.attach(MIMEText(html_body, "html"))

    for image in inline_images or []:
        image_part = MIMEImage(image.content, _subtype=image.content_type.rsplit("/", 1)[-1])
        image_part.add_header("Content-ID", f"<{image.content_id}>")
        image_part.add_header("Content-Disposition", "inline", filename=image.content_id)
        body_related.attach(image_part)

    message.attach(body_related)

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
