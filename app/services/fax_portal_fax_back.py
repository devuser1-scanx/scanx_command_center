from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations import gcs_faxes
from app.integrations.gmail_client import GmailApiError, send_email
from app.integrations.westfax_client import FaxAttachment
from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpCallLog,
    CCFpCallResult,
    CCFpFax,
    CCFpMessageLog,
    CCFpReferralBoard,
    CCFpReferralExam,
    CCFpStatus,
)
from app.schemas.fax_portal import FaxBackDraftResponse, FaxBackRequest, FaxBackResponse
from app.services.fax import WESTFAX_EMAIL_TO_FAX_DOMAIN
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_referrals import (
    DIDNT_RESPOND,
    FAX_PENDING,
    REVIEW,
    MoveExtra,
    ReferralError,
    ReferralNotFoundError,
    move_referral,
)

# Sends the referring physician a fax (through the same WestFax email-to-fax
# gateway the patient profile's Fax button uses) saying we could not reach the
# patient. The body lists the contact attempts: when, by phone or text, and the
# result. Never the text of a message, and never an agent's name ("Agent").
#
# Never log names, numbers or message text here: only ids and counts.
logger = logging.getLogger(__name__)

R = CCFpReferralBoard

BUSINESS_TZ = ZoneInfo("America/Chicago")

# A US fax number: area code, exchange and line.
MIN_FAX_DIGITS = 10

# What the activity log says the person who made each attempt was.
AGENT_LABEL = "Agent"

_DELIVERED = {"delivered", "read"}
_NOT_DELIVERED = {"failed", "undelivered"}


class FaxBackError(ReferralError):
    """The fax cannot be sent as it stands; the message says what to fix."""

    status_code = 422


class FaxBackSendError(ReferralError):
    status_code = 502


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _plain(value: str | None) -> str:
    """Just the digits of a fax number, without the US country code: 9725550100."""
    digits = _digits(value)

    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def _fax_address(digits: str) -> str:
    """9725550100@westfax.com: where WestFax's email-to-fax sends the fax."""
    return f"{digits}@{WESTFAX_EMAIL_TO_FAX_DOMAIN}"


def _when(value: datetime) -> str:
    local = value.astimezone(BUSINESS_TZ)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"

    return f"{local:%b} {local.day}, {local.year} {hour}:{local.minute:02d} {suffix} CT"


def _day(value: datetime) -> str:
    local = value.astimezone(BUSINESS_TZ)

    return f"{local:%b} {local.day}, {local.year}"


def _patient_name(card: R) -> str:
    return " ".join(x for x in (card.first_name, card.last_name) if x) or "Unknown patient"


def default_subject(card: R) -> str:
    name = _patient_name(card)

    return f"Referral Update {name}: Unable to reach patient {name} after multiple attempts"


def _load(db: Session, referral_id: int, *, lock: bool = False) -> R:
    query = select(R).where(R.id == referral_id)

    if lock:
        query = query.with_for_update()

    card = db.scalar(query)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    return card


def _default_destination(db: Session, card: R) -> tuple[str, str]:
    """The fax number the referral came from (the sending fax machine), else the
    referring physician's fax number on the card. Returns (number, where it is
    from); a blank number when neither is known."""
    fax = db.get(CCFpFax, card.fax_id)
    from_fax = _plain(fax.originating_e164 if fax else None)

    if len(from_fax) >= MIN_FAX_DIGITS:
        return _fax_address(from_fax), "The fax number this referral was received from."

    on_card = _plain(card.physician_fax_no)

    if len(on_card) >= MIN_FAX_DIGITS:
        return _fax_address(on_card), "The referring physician's fax number on the referral."

    return "", "No fax number is on file for the referring physician: enter it."


def _original_fax_available(db: Session, card: R) -> bool:
    fax = db.get(CCFpFax, card.fax_id)

    return fax is not None and bool(fax.gcs_uri or fax.job_id)


def _original_fax(db: Session, card: R) -> FaxAttachment:
    """The fax this referral came from, as a PDF attachment (fetched through the
    backend like the Intake Review viewer; the bucket is never exposed)."""
    fax = db.get(CCFpFax, card.fax_id)
    unavailable = FaxBackError(
        'The original fax PDF is not available. Untick "Attach the original fax" to send '
        "without it."
    )

    if fax is None:
        raise unavailable

    try:
        if fax.gcs_uri is None:
            object_name = gcs_faxes.find_fax_pdf(fax.job_id) if fax.job_id else None

            if object_name is None:
                raise unavailable

            fax.gcs_uri = gcs_faxes.to_gcs_uri(object_name)
            fax.gcs_received_at = func.now()
            db.flush()

        data = gcs_faxes.download_fax_pdf(fax.gcs_uri)
    except gcs_faxes.FaxPdfNotFoundError as exc:
        raise unavailable from exc
    except FaxBackError:
        raise
    except Exception as exc:
        logger.warning("Original fax for referral %s could not be fetched.", card.id)
        raise FaxBackSendError("The original fax PDF could not be retrieved.") from exc

    return FaxAttachment(
        filename="original-referral-fax.pdf", content=data, content_type="application/pdf"
    )


# ---------------------------------------------------------------------
# What was tried
# ---------------------------------------------------------------------


def _attempts(db: Session, card: R) -> list[tuple[datetime, str, str]]:
    """(when, kind, result) for every call that got a result and every text,
    oldest first. Message text and agent names are not included."""
    labels = dict(db.execute(select(CCFpCallResult.code, CCFpCallResult.label)).all())
    rows: list[tuple[datetime, str, str]] = []

    for call in db.scalars(
        select(CCFpCallLog)
        .where(CCFpCallLog.referral_id == card.id, CCFpCallLog.result.is_not(None))
        .order_by(CCFpCallLog.started_at.asc(), CCFpCallLog.id.asc())
    ):
        rows.append(
            (call.started_at, "Phone call", labels.get(call.result or "", call.result or ""))
        )

    for message in db.scalars(
        select(CCFpMessageLog)
        .where(CCFpMessageLog.referral_id == card.id)
        .order_by(CCFpMessageLog.created_at.asc(), CCFpMessageLog.id.asc())
    ):
        if message.direction == "inbound":
            rows.append((message.created_at, "Text message", "Patient replied"))
        elif message.status in _DELIVERED:
            rows.append((message.created_at, "Text message", "Sent, delivered"))
        elif message.status in _NOT_DELIVERED:
            rows.append((message.created_at, "Text message", "Sent, not delivered"))
        elif message.status != "queued":
            rows.append((message.created_at, "Text message", "Sent"))

    return sorted(rows, key=lambda row: row[0])


def build_body(db: Session, card: R) -> str:
    """The fax text: who, what was tried, and what we are doing about it."""
    attempts = _attempts(db, card)
    exam = db.scalar(
        select(CCFpReferralExam.appointment_type).where(
            CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == 1
        )
    )
    received = _day(card.created_at)
    calls = sum(1 for _, kind, _ in attempts if kind == "Phone call")
    texts = sum(1 for _, kind, _ in attempts if kind == "Text message")

    lines = [f"Re: Referral for {_patient_name(card)}"]

    if card.dob:
        lines[0] += f" (date of birth {card.dob:%m/%d/%Y})"

    if exam:
        lines.append(f"Exam requested: {exam}")

    lines.append(f"Referral received: {received}")
    lines.append("")
    lines.append(f"To {card.physician_name or 'the referring provider'}:")
    lines.append("")

    made = []

    if calls:
        made.append(f"{calls} phone call{'s' if calls != 1 else ''}")

    if texts:
        made.append(f"{texts} text message{'s' if texts != 1 else ''}")

    tried = " and ".join(made) if made else "several attempts"
    span = (
        f" between {_day(attempts[0][0])} and {_day(attempts[-1][0])}"
        if attempts and _day(attempts[0][0]) != _day(attempts[-1][0])
        else ""
    )
    lines.append(
        "ScanX Health received the referral above and tried to reach the patient to "
        f"schedule the exam. Despite {tried}{span}, we were unable to reach the patient "
        "and have not had a response, so we are unable to schedule the exam and are "
        "closing this referral."
    )
    lines.append("")
    lines.append("If the exam is still needed, please let the patient know to contact us.")

    if attempts:
        lines.append("")
        lines.append("Contact attempts:")

        for number, (when, kind, result) in enumerate(attempts, start=1):
            lines.append(f"{number}. {_when(when)} - {kind} - {result} - {AGENT_LABEL}")

    lines.append("")
    lines.append("Thank you,")
    lines.append("ScanX Health")

    return "\n".join(lines)


def build_draft(db: Session, referral_id: int) -> FaxBackDraftResponse:
    """What the Send fax pop-up starts with; everything in it can be changed."""
    card = _load(db, referral_id)
    number, source = _default_destination(db, card)

    return FaxBackDraftResponse(
        destination_number=number,
        destination_source=source,
        subject=default_subject(card),
        body=build_body(db, card),
        can_mark_didnt_respond=card.status == FAX_PENDING,
        original_fax_available=_original_fax_available(db, card),
    )


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------


def _to_html(text: str) -> str:
    """The typed text as a simple HTML body: escaped, line breaks kept."""
    escaped = html.escape(text.replace("\r\n", "\n"))

    return (
        '<div style="font-family: Arial, sans-serif; font-size: 12pt;">'
        + escaped.replace("\n", "<br>\n")
        + "</div>"
    )


def send_fax_back(
    db: Session,
    referral_id: int,
    payload: FaxBackRequest,
    *,
    user: CCUser,
    role_codes: set[str],
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> FaxBackResponse:
    card = _load(db, referral_id, lock=True)

    if card.status == REVIEW:
        raise FaxBackError("Release this referral from Intake Review first.")

    status_row = db.get(CCFpStatus, card.status)

    if status_row is not None and not status_row.is_open:
        raise FaxBackError("This referral is closed.")

    if payload.mark_didnt_respond and card.status != FAX_PENDING:
        raise FaxBackError(
            "Only a referral in Fax Provider Name Pending can be moved to Didn't Respond."
        )

    digits = _plain(payload.destination_number)

    if len(digits) < MIN_FAX_DIGITS:
        raise FaxBackError("Enter the referring physician's full fax number.")

    body = payload.body.strip()

    if not body:
        raise FaxBackError("The fax needs a message.")

    subject = (payload.subject or "").strip() or default_subject(card)
    attachments = [_original_fax(db, card)] if payload.attach_original else []

    try:
        message_id = send_email(
            to=[_fax_address(digits)],
            cc=[settings.fax_feedback_email],
            bcc=[],
            subject=subject,
            html_body=_to_html(body),
            attachments=attachments,
        )
    except RuntimeError as exc:
        raise FaxBackSendError("Faxing is not set up.") from exc
    except GmailApiError as exc:
        logger.warning("Fax back for referral %s could not be sent.", card.id)
        log_activity(
            db,
            section="referral_case",
            action="fax_back_failed",
            user_id=user.id,
            referral_id=card.id,
            fax_id=card.fax_id,
            details={"destination_last4": digits[-4:]},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        # Saved before the error is raised: the route rolls back on an error.
        db.commit()

        raise FaxBackSendError(f"The fax could not be sent: {exc.message}") from exc

    moved_to: str | None = None

    if payload.mark_didnt_respond:
        move_referral(
            db,
            card.id,
            DIDNT_RESPOND,
            source="button",
            user=user,
            role_codes=role_codes,
            extra=MoveExtra(),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        moved_to = DIDNT_RESPOND

    log_activity(
        db,
        section="referral_case",
        action="fax_back",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        details={
            "destination_last4": digits[-4:],
            "email_message_id": message_id,
            "attached_original": bool(attachments),
            "moved_to": moved_to,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.flush()

    return FaxBackResponse(
        destination_number=_fax_address(digits),
        email_message_id=message_id,
        moved_to=moved_to,
        row_version=card.row_version,
        sent_at=datetime.now(UTC),
    )
