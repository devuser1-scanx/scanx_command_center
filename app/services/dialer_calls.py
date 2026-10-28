from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.phone import normalize_e164
from app.db.prod_session import get_prod_session_factory
from app.db.session import SessionLocal
from app.integrations.twilio_client import build_voice_access_token, voice_token_configured
from app.models.auth import CCUser
from app.models.calls import CCCallLog
from app.repositories import calls as calls_repository
from app.services import fax_portal_calls
from app.services.audit import record_user_activity

# Calls placed from the Calls module (the Web Dialer): the staff member talks
# through the browser (Twilio Voice SDK) and Twilio dials the patient.
#
# The token Command Center issues names the standalone Web Dialer's TwiML App, so
# Twilio asks the standalone service what to do with the call. That service dials,
# records, writes the production `call_logs` row and its `dialer_call_events`, and
# sends the missed-call alerts, exactly as it does for its own frontend. This
# module therefore never talks to Twilio about a call. What it adds:
#   * who may place calls (calls.create), checked before a token is issued;
#   * cc_call_logs, Command Center's own record of every call and who placed it,
#     plus audit entries when a call is requested and when it ends; and
#   * the link between the two records: the browser sends the Command Center call
#     id to Twilio as `CcCallId`, the standalone service stores it in the call's
#     event, and sync_open_calls reads it back and then follows the production
#     row's status.
#
# Never log phone numbers or names here: only ids and Twilio codes.
logger = logging.getLogger(__name__)

# The custom parameter the browser connects with. The standalone service ignores
# parameters it does not know but keeps them in `dialer_call_events`.
CC_CALL_PARAM = "CcCallId"

QUEUED = "queued"
ABANDONED = "abandoned"

# Twilio's progress on a call only moves forward.
PROGRESS_RANK = {"queued": 0, "initiated": 1, "ringing": 2, "in-progress": 3}
TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled"}
ACTIVE = set(PROGRESS_RANK)

# A call row that never reached Twilio (the browser was closed, the microphone was
# refused, the standalone service was down) is dropped after this long.
STALE_QUEUED_AFTER = timedelta(minutes=2)

# Calls are followed in production for this long after they start, and for this
# long after the browser reported them over (their final status can lag).
FOLLOW_FOR = timedelta(hours=6)
FOLLOW_AFTER_END = timedelta(minutes=10)


class DialerCallError(Exception):
    """The call could not be placed; the message says why."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def calling_configured() -> bool:
    return bool(
        settings.dialer_twiml_app_sid and voice_token_configured(settings.dialer_twiml_app_sid)
    )


@contextmanager
def _prod_session() -> Iterator[Session]:
    session = get_prod_session_factory()()

    try:
        yield session
    finally:
        session.close()


def _normalise(status: str | None) -> str:
    value = (status or "").strip().lower()

    return "canceled" if value == "cancelled" else value


def _audit(
    db: Session,
    call: CCCallLog,
    action: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Ids and outcomes only: the number dialled is not written to the audit log."""
    record_user_activity(
        db,
        actor_user_id=call.user_id,
        action=action,
        resource_type="call",
        resource_id=str(call.id),
        details={
            "direction": call.direction,
            "status": call.status,
            "duration_s": call.duration_s,
            "appointment_id": call.appointment_id,
            "prod_call_log_id": call.prod_call_log_id,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _abandon_stale(db: Session, now: datetime) -> None:
    db.execute(
        update(CCCallLog)
        .where(
            CCCallLog.status == QUEUED,
            CCCallLog.twilio_call_sid.is_(None),
            CCCallLog.started_at < now - STALE_QUEUED_AFTER,
        )
        .values(status=ABANDONED, ended_at=now, updated_at=func.now())
    )


# ---------------------------------------------------------------------
# Starting and ending a call
# ---------------------------------------------------------------------


def start_call(
    db: Session,
    user: CCUser,
    *,
    number: str,
    appointment_id: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[int, str, str]:
    """Logs a call to be placed and returns its id, a short-lived token, and the
    validated number (E.164). The browser connects to Twilio with the token, the
    number and the call id. Raises DialerCallError."""
    if not calling_configured():
        raise DialerCallError("Calling is not set up yet.", 503)

    to_number = normalize_e164(number)

    if to_number is None:
        raise DialerCallError("Enter a valid phone number, for example +14155550123.", 422)

    try:
        token = build_voice_access_token(
            identity=fax_portal_calls.client_identity(user.id),
            twiml_app_sid=settings.dialer_twiml_app_sid,
        )
    except RuntimeError as exc:
        raise DialerCallError("Calling is not set up yet.", 503) from exc

    # Calls that never reached Twilio must not linger.
    _abandon_stale(db, datetime.now(UTC))

    call = CCCallLog(
        user_id=user.id,
        direction="outbound",
        to_number=to_number,
        appointment_id=appointment_id or None,
        status=QUEUED,
    )
    db.add(call)
    db.flush()

    _audit(db, call, "call.requested", ip_address=ip_address, user_agent=user_agent)
    db.commit()

    return call.id, token, to_number


def end_call(
    db: Session,
    call_id: int,
    user: CCUser,
    *,
    reached_twilio: bool,
    duration_s: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """The browser reports its call over. A call that never reached Twilio is
    dropped; otherwise the end is recorded (the standalone service's own status
    can lag behind, and may never advance). Only the caller's own call can be
    ended. Returns whether there was such a call."""
    call = db.scalar(select(CCCallLog).where(CCCallLog.id == call_id).with_for_update())

    if call is None or call.user_id != user.id:
        return False

    if call.status == ABANDONED or call.ended_at is not None:
        return True

    now = datetime.now(UTC)

    if not reached_twilio and call.twilio_call_sid is None and call.status == QUEUED:
        call.status = ABANDONED
        call.ended_at = now
        call.updated_at = func.now()
        _audit(db, call, "call.abandoned", ip_address=ip_address, user_agent=user_agent)
        db.commit()

        return True

    call.ended_at = now
    call.duration_s = call.duration_s if call.duration_s is not None else max(0, duration_s)
    call.updated_at = func.now()
    _audit(db, call, "call.ended", ip_address=ip_address, user_agent=user_agent)
    db.commit()

    return True


# ---------------------------------------------------------------------
# Following the standalone service's production record
# ---------------------------------------------------------------------


def _open_calls(db: Session, now: datetime) -> list[CCCallLog]:
    return list(
        db.scalars(
            select(CCCallLog)
            .where(
                CCCallLog.status.in_(sorted(ACTIVE)),
                CCCallLog.started_at > now - FOLLOW_FOR,
                (CCCallLog.ended_at.is_(None)) | (CCCallLog.ended_at > now - FOLLOW_AFTER_END),
            )
            .with_for_update()
        )
    )


def _link(db: Session, calls: list[CCCallLog], prod_db: Session) -> None:
    """Finds the production call each unlinked call became."""
    unlinked = {call.id: call for call in calls if call.prod_call_log_id is None}

    for event in calls_repository.find_outgoing_events(prod_db, list(unlinked)):
        call = unlinked.get(int(event["cc_call_id"]))

        if call is None or call.prod_call_log_id is not None:
            continue

        # `From` is the identity Twilio took from the token: only the person the
        # call was issued to can have produced it.
        if event["caller"] != f"client:{fax_portal_calls.client_identity(call.user_id or 0)}":
            continue

        call.prod_call_log_id = int(event["call_log_id"])
        call.updated_at = func.now()


def _follow(db: Session, call: CCCallLog, state: dict[str, Any]) -> bool:
    """Copies the production row's progress onto the call: forward only. Returns
    whether anything changed."""
    changed = False
    sid = state.get("call_sid")

    if sid and call.twilio_call_sid is None:
        taken = db.scalar(select(CCCallLog.id).where(CCCallLog.twilio_call_sid == sid))

        if taken is None:
            call.twilio_call_sid = sid
            changed = True

    status = _normalise(state.get("status"))
    duration = state.get("duration")

    if status in TERMINAL or (
        status in PROGRESS_RANK and PROGRESS_RANK[status] > PROGRESS_RANK.get(call.status, -1)
    ):
        if status != call.status:
            call.status = status
            changed = True

        if status == "in-progress" and call.answered_at is None:
            call.answered_at = datetime.now(UTC)
            changed = True

    if status in TERMINAL:
        if duration:
            call.duration_s = int(duration)
            changed = True

        if call.ended_at is None:
            call.ended_at = datetime.now(UTC)
            changed = True
            _audit(db, call, "call.ended")

    if changed:
        call.updated_at = func.now()

    return changed


def sync_open_calls(db: Session, prod_db: Session) -> int:
    """Brings the open calls up to date with the production call log: links the
    ones the standalone service has now seen, then follows their status and
    duration. Idempotent. Returns how many calls changed."""
    now = datetime.now(UTC)
    _abandon_stale(db, now)
    calls = _open_calls(db, now)

    if not calls:
        db.commit()

        return 0

    _link(db, calls, prod_db)

    states = calls_repository.get_call_log_states(
        prod_db, [call.prod_call_log_id for call in calls if call.prod_call_log_id is not None]
    )
    changed = sum(
        _follow(db, call, states[call.prod_call_log_id])
        for call in calls
        if call.prod_call_log_id in states
    )
    db.commit()

    return changed


def sync_open_calls_once() -> int:
    """sync_open_calls with its own sessions, for the background poll. The
    production database is only touched when there is a call to follow."""
    with SessionLocal() as db:
        if not _open_calls(db, datetime.now(UTC)):
            db.rollback()

            return 0

        with _prod_session() as prod_db:
            return sync_open_calls(db, prod_db)
