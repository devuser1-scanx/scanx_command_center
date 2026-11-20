from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth import CCUser
from app.models.fax_portal import CCFpReferralBoard, CCFpReferralExam, CCFpStatus
from app.schemas.fax_portal import PrefillLinkDraftResponse, SendMessageResponse
from app.services import fax_portal_messages
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_booking import build_booking_link
from app.services.fax_portal_referrals import (
    REVIEW,
    InvalidMoveError,
    ReferralNotFoundError,
)

# "Send prefill link to patient": texts the patient a personal link. Opening it
# redirects to the Acuity scheduling page for their exam and clinic, with the
# patient's details (and the intake questions) already filled in, so they only
# pick a date and time.
#
# The text carries only an opaque signed token: no name, date of birth or
# reason ever sits in the SMS or in our logs. The Acuity URL (which does carry
# those details) is built when the link is opened and sent to the patient's
# browser over HTTPS only. The token expires, and it stops working once the
# referral is booked or closed.
#
# Never log names, numbers, tokens or the Acuity URL: only ids.
logger = logging.getLogger(__name__)

R = CCFpReferralBoard

TOKEN_TYPE = "fp_prefill_booking"
TOKEN_DAYS = 7

# The public path the text points to (see routes/fax_portal_public.py).
LINK_PATH = "/fax-portal/book"

NOT_VALID = "This link is not valid. Please call us and we will help you book."


class LinkError(Exception):
    """The link cannot be used; `message` is safe to show to the patient."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def make_token(referral_id: int, *, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)

    return jwt.encode(
        {"typ": TOKEN_TYPE, "r": referral_id, "exp": issued + timedelta(days=TOKEN_DAYS)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def read_token(token: str) -> int:
    """The referral id a token was made for. Raises LinkError if it is not one
    of ours, was altered, or has expired."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise LinkError("This link has expired. Please call us and we will help you book.") from exc
    except jwt.PyJWTError as exc:
        raise LinkError(NOT_VALID) from exc

    referral_id = claims.get("r")

    if claims.get("typ") != TOKEN_TYPE or not isinstance(referral_id, int):
        raise LinkError(NOT_VALID)

    return referral_id


def _secure_link(referral_id: int) -> str | None:
    base = (settings.public_api_base_url or "").rstrip("/")

    return f"{base}{LINK_PATH}/{make_token(referral_id)}" if base else None


def _exam_name(db: Session, card: R) -> str:
    exam = db.scalar(
        select(CCFpReferralExam.appointment_type).where(
            CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == 1
        )
    )

    return exam.lower() if exam else "ultrasound"


def _first_name(card: R) -> str:
    return (card.first_name or "").strip() or "there"


def _card_text(db: Session, card: R) -> str:
    """The wording of the RCS card (title, body and button), as the agent sees
    it. The Twilio Content Template must say the same; see
    Twilio_Prefill_Link_RCS_Template.md."""
    return (
        "Book your appointment\n\n"
        f"Hi {_first_name(card)}, your {_exam_name(db, card)} is ready to schedule. Tap "
        "Book Appointment to choose a date and time. Your details are already filled in.\n\n"
        f"This secure link is personal to you and expires in {TOKEN_DAYS} days. "
        "Please don't share it.\n\n"
        "[Book Appointment]"
    )


def _message(db: Session, card: R, link: str) -> str:
    """The plain-text version, sent when there is no RCS card (or the phone
    cannot show one)."""
    provider = (card.physician_name or "").strip()
    ordered = f" ordered by {provider}" if provider else ""

    return (
        f"Hi {_first_name(card)}, this is ScanX Health about your "
        f"{_exam_name(db, card)}{ordered}. Tap your personal link to choose a date and "
        f"time. Your details are already filled in: {link} The link is personal to you "
        f"and expires in {TOKEN_DAYS} days. Please don't share it."
    )


def _block_reason(db: Session, card: R) -> str | None:
    """Why the link cannot be sent now, or None."""
    status_row = db.get(CCFpStatus, card.status)

    if card.status == REVIEW:
        return "Release this referral from Intake Review before sending the link."

    if status_row is not None and not status_row.is_open:
        return "This referral is closed."

    if not settings.public_api_base_url:
        return "The public link address is not set up (PUBLIC_API_BASE_URL)."

    _, link_blocked = build_booking_link(db, card)

    if link_blocked:
        return link_blocked

    return fax_portal_messages.text_block_reason(db, card)


def build_draft(db: Session, referral_id: int) -> PrefillLinkDraftResponse:
    """The text the patient will get (with a stand-in where the personal link
    goes) and why it cannot be sent yet, if so."""
    card = db.get(R, referral_id)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    as_card = bool(settings.twilio_prefill_link_content_sid)

    return PrefillLinkDraftResponse(
        message=_card_text(db, card) if as_card else _message(db, card, "[personal link]"),
        as_card=as_card,
        blocked_reason=_block_reason(db, card),
    )


def send_prefill_link(
    db: Session,
    referral_id: int,
    *,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SendMessageResponse:
    """Texts the patient their personal booking link (same rules and record as
    any text: opt-outs, do-not-call, sending hours, the message log). Commits."""
    card = db.get(R, referral_id)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    reason = _block_reason(db, card)

    if reason is not None:
        raise InvalidMoveError(reason)

    link = _secure_link(card.id)

    if link is None:  # pragma: no cover - _block_reason already checked
        raise InvalidMoveError("The public link address is not set up (PUBLIC_API_BASE_URL).")

    content_sid = settings.twilio_prefill_link_content_sid

    result = fax_portal_messages.send_message(
        db,
        referral_id,
        _message(db, card, link),
        None,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
        content_sid=content_sid,
        content_variables=(
            {"1": _first_name(card), "2": _exam_name(db, card), "3": link} if content_sid else None
        ),
    )

    log_activity(
        db,
        section="referral_case",
        action="send_prefill_link",
        user_id=user.id,
        referral_id=referral_id,
        fax_id=card.fax_id,
        details={"message_id": result.message_id},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    return result


def open_link(
    db: Session,
    token: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """The Acuity URL a valid link leads to. Raises LinkError (message safe for
    the patient) otherwise. Records that the patient opened it."""
    referral_id = read_token(token)
    card = db.get(R, referral_id)

    if card is None:
        raise LinkError(NOT_VALID)

    status_row = db.get(CCFpStatus, card.status)

    if status_row is not None and not status_row.is_open:
        raise LinkError(
            "This request is already complete. If you still need an appointment, please call us."
        )

    url, blocked = build_booking_link(db, card, include_intake_fields=True)

    if url is None:
        logger.warning("Prefill link for referral %s could not be built: %s", card.id, blocked)

        raise LinkError("We could not open the booking page. Please call us and we will help.")

    log_activity(
        db,
        section="referral_case",
        action="prefill_link_opened",
        referral_id=card.id,
        fax_id=card.fax_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    return url
