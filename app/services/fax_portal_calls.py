from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.twilio_client import build_voice_access_token
from app.models.auth import CCUser
from app.models.fax_portal import CCFpCallLog
from app.schemas.fax_portal import DialCallResponse
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_case import (
    STARTING,
    UNCONNECTED_AFTER,
    _optout,
    start_call_log,
    voice_configured,
)
from app.services.fax_portal_referrals import ReferralError, ReferralNotFoundError

# Click-to-call from the browser (Twilio Voice SDK): the staff member talks
# through their computer, and Twilio dials the patient from the organisation's
# number (TWILIO_VOICE_FROM_NUMBER). The patient's number never reaches the
# browser: Twilio asks our server what to do (CLIENT_PATH) and the server dials
# the number stored on the call row.
#
# Never log phone numbers or names here: only ids and Twilio codes.
logger = logging.getLogger(__name__)

VOICE_PATH = "/fax-portal/webhooks/twilio/voice"
CLIENT_PATH = f"{VOICE_PATH}/client"
DIAL_DONE_PATH = f"{VOICE_PATH}/dial-done"
STATUS_PATH = f"{VOICE_PATH}/status"

# How long the patient's phone rings before Twilio gives up.
PATIENT_RING_SECONDS = 30

# Twilio's progress on the browser leg only moves forward.
CALL_STATUS_RANK = {
    "queued": 0,
    "initiated": 1,
    "ringing": 2,
    "in-progress": 3,
    "completed": 4,
    "busy": 5,
    "no-answer": 5,
    "failed": 5,
    "canceled": 5,
}

# The browser leg never connected: this was not an attempt on the patient.
AGENT_UNREACHED = {"busy", "no-answer", "failed", "canceled"}

PATIENT_PREFIX = "patient:"


class CallServiceError(ReferralError):
    """The call could not be placed; the message says why."""

    status_code = 502


def _url(path: str) -> str:
    return f"{(settings.public_api_base_url or '').rstrip('/')}{path}"


def signed_url(path: str) -> str | None:
    """The URL Twilio signed for one of the voice webhooks, when the public base
    URL is configured (behind Cloud Run the request's own scheme and host can
    differ from what Twilio was given)."""
    return _url(path) if settings.public_api_base_url else None


def client_identity(user_id: int) -> str:
    """The name the browser connects to Twilio as; Twilio reports it back as
    `client:<identity>`, which is how a call row is tied to its caller."""
    return f"user-{user_id}"


def _twiml(inner: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>'


def _by_sid(db: Session, call_sid: str) -> CCFpCallLog | None:
    return db.scalar(select(CCFpCallLog).where(CCFpCallLog.twilio_call_sid == call_sid))


def _discard_call(
    db: Session,
    call_id: int,
    *,
    action: str,
    user_id: int | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Removes a call row that never reached the patient (the call is not an
    attempt and must not block the next one), leaving an audit entry."""
    call = db.get(CCFpCallLog, call_id)

    if call is None:
        return

    referral_id = call.referral_id
    db.delete(call)

    log_activity(
        db,
        section="referral_case",
        action=action,
        user_id=user_id,
        referral_id=referral_id,
        details={"call_id": call_id},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()


# ---------------------------------------------------------------------
# Placing the call
# ---------------------------------------------------------------------


def dial_patient(
    db: Session,
    referral_id: int,
    *,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DialCallResponse:
    """Starts a click-to-call: logs the call (same rules as logging one by
    hand: row lock, closed/do-not-call/limit/in-progress checks) and returns the
    call id with a short-lived token. The browser then connects to Twilio with
    that token and the call id; Twilio asks CLIENT_PATH who to dial."""
    if not voice_configured():
        raise CallServiceError("Click-to-call is not set up yet.")

    _drop_unconnected(db, referral_id, user, ip_address, user_agent)

    call = start_call_log(
        db,
        referral_id,
        user=user,
        from_number=settings.twilio_voice_from_number,
        source="twilio",
        ip_address=ip_address,
        user_agent=user_agent,
    )
    call.twilio_status = STARTING
    call_id = call.id
    # Committed before the browser connects: Twilio asks CLIENT_PATH as soon as
    # it does, and the row must be there to read.
    db.commit()

    try:
        token = build_voice_access_token(identity=client_identity(user.id))
    except RuntimeError as exc:
        _discard_call(
            db,
            call_id,
            action="call_start_failed",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        raise CallServiceError("Click-to-call is not set up yet.") from exc

    call = db.get(CCFpCallLog, call_id)

    return DialCallResponse(call_id=call_id, started_at=call.started_at, token=token)


def _drop_unconnected(
    db: Session,
    referral_id: int,
    user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Clears calls this referral started that never reached Twilio (the browser
    was closed, or the microphone was refused) so they do not linger."""
    stale = db.scalars(
        select(CCFpCallLog.id).where(
            CCFpCallLog.referral_id == referral_id,
            CCFpCallLog.twilio_status == STARTING,
            CCFpCallLog.result.is_(None),
            CCFpCallLog.started_at < datetime.now(UTC) - UNCONNECTED_AFTER,
        )
    ).all()

    for call_id in stale:
        _discard_call(
            db,
            call_id,
            action="call_abandoned",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def abandon_call(
    db: Session,
    call_id: int,
    *,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """The browser could not start the call (microphone refused, no connection):
    removes the row so it does not count or block the next call. Only the
    caller's own call that never reached Twilio can be removed."""
    call = db.get(CCFpCallLog, call_id)

    if (
        call is None
        or call.user_id != user.id
        or call.twilio_call_sid is not None
        or call.result is not None
    ):
        raise ReferralNotFoundError("Call not found.")

    _discard_call(
        db,
        call_id,
        action="call_abandoned",
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


# ---------------------------------------------------------------------
# Twilio asking what to do, and reporting back
# ---------------------------------------------------------------------


def _hangup(message: str) -> str:
    return _twiml(f"<Say>{escape(message)}</Say><Hangup/>")


def client_twiml(db: Session, params: dict[str, str]) -> str:
    """TwiML for a call the browser just started: dial the patient, showing the
    organisation's number. The number comes from the call row, never from the
    request, and the row must belong to the browser that is calling and not have
    been used before."""
    raw_id = params.get("CallId", "")
    call = db.get(CCFpCallLog, int(raw_id)) if raw_id.isdigit() else None

    if (
        call is None
        or call.result is not None
        or call.twilio_call_sid is not None
        or not call.to_number
        or params.get("From") != f"client:{client_identity(call.user_id or 0)}"
    ):
        return _hangup("This call could not be connected.")

    optout = _optout(db, call.to_number)

    if optout is not None and optout.dnc_at is not None:
        return _hangup("This number is on the do not call list.")

    call.twilio_call_sid = params.get("CallSid") or None
    call.twilio_status = "initiated"
    call.updated_at = func.now()

    caller_id = call.from_number or settings.twilio_voice_from_number or ""

    return _twiml(
        f'<Dial callerId={quoteattr(caller_id)} answerOnBridge="true" '
        f'timeout="{PATIENT_RING_SECONDS}" action={quoteattr(_url(DIAL_DONE_PATH))} '
        'method="POST">'
        f"<Number>{escape(call.to_number)}</Number>"
        "</Dial>"
    )


def apply_dial_result(
    db: Session,
    *,
    call_sid: str,
    dial_status: str | None,
    dial_duration: str | None,
) -> bool:
    """Records how the patient's leg went (Twilio's <Dial> action callback).
    Returns whether a known call was found."""
    call = _by_sid(db, call_sid)

    if call is None:
        return False

    status = (dial_status or "").strip().lower()

    if status:
        call.twilio_status = f"{PATIENT_PREFIX}{status}"[:50]

    if status in ("completed", "answered"):
        seconds = int(dial_duration) if dial_duration and dial_duration.isdigit() else None

        if seconds is not None:
            call.duration_s = seconds
            call.answered_at = datetime.now(UTC) - timedelta(seconds=seconds)

    call.updated_at = func.now()

    return True


def apply_call_status(
    db: Session,
    *,
    call_sid: str,
    call_status: str | None,
) -> bool:
    """Updates a call from Twilio's status callback on the browser's leg. Only
    moves forward, and never overwrites the patient's outcome. A call whose
    browser leg never connected is removed: it was not an attempt on the
    patient. Returns whether a known call was found."""
    call = _by_sid(db, call_sid)

    if call is None:
        return False

    status = (call_status or "").strip().lower()
    patient_outcome = (call.twilio_status or "").startswith(PATIENT_PREFIX)

    if (
        status in AGENT_UNREACHED
        and not patient_outcome
        and call.result is None
        and call.answered_at is None
    ):
        _discard_call(
            db,
            call.id,
            action="call_agent_unreachable",
            user_id=call.user_id,
        )

        return True

    if not patient_outcome and CALL_STATUS_RANK.get(status, -1) > CALL_STATUS_RANK.get(
        call.twilio_status or "", -1
    ):
        call.twilio_status = status[:50]

    if status == "completed" and call.ended_at is None:
        call.ended_at = datetime.now(UTC)

    call.updated_at = func.now()

    return True


def no_content_twiml() -> str:
    """What to answer Twilio with after the patient leg ends: hang up."""
    return _twiml("<Hangup/>")
