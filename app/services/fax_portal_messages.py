from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.twilio_client import TwilioApiError, send_sms
from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpContactOptout,
    CCFpMessageLog,
    CCFpReferralBoard,
    CCFpReferralExam,
)
from app.schemas.fax_portal import MessageTemplateResponse, SendMessageResponse
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_booking import build_booking_link
from app.services.fax_portal_case import text_block_reason
from app.services.fax_portal_referrals import (
    InvalidMoveError,
    ReferralError,
    ReferralNotFoundError,
)

# Never log message bodies, phone numbers or names here: only ids and codes.
logger = logging.getLogger(__name__)

R = CCFpReferralBoard

# Twilio's error for "the recipient replied STOP".
TWILIO_UNSUBSCRIBED = 21610

STATUS_CALLBACK_PATH = "/fax-portal/webhooks/twilio/message-status"

# Delivery progresses forward only: updates can arrive out of order.
STATUS_RANK = {
    "queued": 0,
    "accepted": 1,
    "scheduled": 1,
    "sending": 1,
    "sent": 2,
    "delivered": 3,
    "read": 4,
    "undelivered": 5,
    "failed": 5,
}


class MessageSendError(ReferralError):
    """The text could not be sent; the message says why."""

    status_code = 502


# ---------------------------------------------------------------------
# Templates (approved wording; every one ends with the opt-out line)
# ---------------------------------------------------------------------

TEMPLATES: dict[str, tuple[str, str, str]] = {
    "booking_link_en": (
        "Booking link (EN)",
        "en",
        "Hi {patient_first}, this is ScanX calling on behalf of {provider} about your "
        "{exam}. Book a time that works for you: {booking_link} Reply STOP to opt out.",
    ),
    "booking_link_es": (
        "Booking link (ES)",
        "es",
        "Hola {patient_first}, le escribe ScanX de parte de {provider} para agendar su "
        "{exam}. Reserve aqui: {booking_link} Responda STOP para no recibir mensajes.",
    ),
    "missed_call_en": (
        "Missed call (EN)",
        "en",
        "Hi {patient_first}, we just tried calling from ScanX about the {exam} "
        "{provider} ordered. Book online at {booking_link} or call us back. "
        "Reply STOP to opt out.",
    ),
    "callback_confirmation_en": (
        "Callback confirmation (EN)",
        "en",
        "Hi {patient_first}, this is ScanX confirming we will call you back about your "
        "{exam}. If another time is better, book here: {booking_link} Reply STOP to opt out.",
    ),
}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _context(db: Session, card: R) -> dict[str, str | None]:
    exam = db.scalar(
        select(CCFpReferralExam.appointment_type).where(
            CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == 1
        )
    )
    link, _ = build_booking_link(db, card)

    return {
        "patient_first": (card.first_name or "").strip() or None,
        "exam": exam.lower() if exam else None,
        "provider": (card.physician_name or "").strip() or None,
        "booking_link": link,
    }


def render_templates(db: Session, referral_id: int) -> list[MessageTemplateResponse]:
    """Every template with this referral's details filled in. Anything not
    known yet stays as a {placeholder} and is listed in `missing`, so the
    agent fills it in before sending."""
    card = db.get(R, referral_id)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    context = _context(db, card)
    rendered = []

    for code, (label, language, body) in TEMPLATES.items():
        missing: list[str] = []

        def fill(match: re.Match[str], missing: list[str] = missing) -> str:
            name = match.group(1)
            value = context.get(name)

            if value:
                return value

            if name not in missing:
                missing.append(name)

            return match.group(0)

        rendered.append(
            MessageTemplateResponse(
                code=code,
                label=label,
                language=language,
                body=_PLACEHOLDER.sub(fill, body),
                missing=missing,
            )
        )

    return rendered


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------


def _mark_opted_out(db: Session, phone_e164: str, source: str) -> None:
    optout = db.get(CCFpContactOptout, phone_e164)

    if optout is None:
        optout = CCFpContactOptout(phone_e164=phone_e164)
        db.add(optout)

    optout.sms_opt_out_at = optout.sms_opt_out_at or func.now()
    optout.source = optout.source or source
    optout.updated_at = func.now()


def send_message(
    db: Session,
    referral_id: int,
    body: str,
    template_code: str | None,
    *,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SendMessageResponse:
    """Texts the patient (RCS through the Twilio Messaging Service, falling
    back to SMS) and records it in cc_fp_message_log.

    Refused when the referral is closed or not released yet, the number is on
    the do-not-call list or opted out, or it is outside the sending hours. The
    row is committed before Twilio is called, so there is a record even if the
    request dies mid-send; this function therefore commits.
    """
    card = db.scalar(select(R).where(R.id == referral_id).with_for_update())

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    if template_code is not None and template_code not in TEMPLATES:
        raise InvalidMoveError("Unknown message template.")

    reason = text_block_reason(db, card)

    if reason is not None:
        raise InvalidMoveError(reason)

    text = body.strip()

    if not text:
        raise InvalidMoveError("Write a message first.")

    if _PLACEHOLDER.search(text):
        raise InvalidMoveError("Fill in the {placeholders} in the message before sending.")

    phone = card.phone_e164 or ""
    message = CCFpMessageLog(
        referral_id=card.id,
        direction="outbound",
        channel="sms",
        user_id=user.id,
        template_code=template_code,
        to_number=phone,
        body=text,
        status="queued",
    )
    db.add(message)
    db.flush()
    message_id = message.id
    db.commit()

    callback = (
        f"{settings.public_api_base_url.rstrip('/')}{STATUS_CALLBACK_PATH}"
        if settings.public_api_base_url
        else None
    )

    try:
        result = send_sms(to=phone, body=text, status_callback=callback)
    except TwilioApiError as exc:
        _record_failure(db, message_id, phone, exc.code, exc.message, user, ip_address, user_agent)

        if exc.code == TWILIO_UNSUBSCRIBED:
            raise InvalidMoveError("The patient has opted out of text messages.") from exc

        raise MessageSendError(f"Could not send the text: {exc.message}") from exc
    except RuntimeError as exc:
        _record_failure(db, message_id, phone, None, str(exc), user, ip_address, user_agent)

        raise MessageSendError("Text messaging is not configured.") from exc

    message = db.get(CCFpMessageLog, message_id)
    card = db.get(R, referral_id)
    channel = "rcs" if (result.from_number or "").lower().startswith("rcs:") else "sms"

    message.twilio_message_sid = result.message_sid
    message.from_number = (result.from_number or "")[:20] or None
    message.channel = channel
    message.status = "sent"
    message.status_at = func.now()
    message.updated_at = func.now()
    card.last_contact_at = func.now()
    card.updated_at = func.now()

    log_activity(
        db,
        section="referral_case",
        action="message_send",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        details={"message_id": message_id, "channel": channel, "template": template_code},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    return SendMessageResponse(message_id=message_id, status="sent", channel=channel)


def _record_failure(
    db: Session,
    message_id: int,
    phone: str,
    code: int | None,
    detail: str,
    user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    message = db.get(CCFpMessageLog, message_id)

    message.status = "failed"
    message.status_at = func.now()
    message.error_code = str(code) if code is not None else None
    message.error_message = detail[:500]
    message.updated_at = func.now()

    if code == TWILIO_UNSUBSCRIBED and phone:
        _mark_opted_out(db, phone, "stop_reply")

    log_activity(
        db,
        section="referral_case",
        action="message_failed",
        user_id=user.id,
        referral_id=message.referral_id,
        details={"message_id": message_id, "error_code": code},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    logger.warning("Text message %s failed (Twilio error %s).", message_id, code)


# ---------------------------------------------------------------------
# Twilio's delivery updates
# ---------------------------------------------------------------------


def apply_status_callback(
    db: Session,
    *,
    message_sid: str,
    status: str,
    error_code: str | None = None,
    from_number: str | None = None,
) -> bool:
    """Updates a message from Twilio's status callback. Only moves a message
    forward (an "sent" arriving after "delivered" is ignored). Returns whether
    a known message was found."""
    message = db.scalar(
        select(CCFpMessageLog).where(CCFpMessageLog.twilio_message_sid == message_sid)
    )

    if message is None:
        return False

    new_status = status.lower()

    if STATUS_RANK.get(new_status, -1) > STATUS_RANK.get(message.status, -1):
        message.status = new_status[:30]
        message.status_at = datetime.now(UTC)
        message.updated_at = func.now()

    if error_code:
        message.error_code = error_code[:20]

    if from_number and from_number.lower().startswith("rcs:"):
        message.channel = "rcs"

    if error_code == str(TWILIO_UNSUBSCRIBED) and message.to_number:
        _mark_opted_out(db, message.to_number, "stop_reply")

    db.flush()

    return True
