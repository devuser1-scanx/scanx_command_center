from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.phone import normalize_e164
from app.integrations.twilio_client import voice_token_configured
from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpCallLog,
    CCFpCallResult,
    CCFpContactOptout,
    CCFpFax,
    CCFpMessageLog,
    CCFpReferralBoard,
    CCFpReferralExam,
    CCFpReferralStatusHistory,
    CCFpStatus,
)
from app.schemas.fax_portal import (
    CALL_LIMIT,
    AssigneeResponse,
    CallResultOption,
    CallResultRequest,
    CallResultResponse,
    CaseCall,
    CaseDetailResponse,
    CaseFax,
    CaseFields,
    CaseMessage,
    CaseOutreach,
    ClinicChoice,
    DoNotCallResponse,
    ExamChoice,
    ReferralSearchItem,
    TimelineEvent,
)
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_board import _full_name, _initials
from app.services.fax_portal_referrals import (
    DECLINED,
    FAX_PENDING,
    REVIEW,
    WORKING,
    InvalidMoveError,
    MoveExtra,
    MoveForbiddenError,
    ReferralError,
    ReferralNotFoundError,
    move_referral,
)

R = CCFpReferralBoard

BUSINESS_TZ = ZoneInfo("America/Chicago")

# Texts go out between these local hours only (TCPA quiet hours).
TEXT_FROM_HOUR = 8
TEXT_UNTIL_HOUR = 21

# Default next touch after an unsuccessful call: the next business day at 9 AM.
NEXT_TOUCH_HOUR = 9

# A call started but never given a result stops blocking a new one after this.
STALE_CALL_AFTER = timedelta(hours=2)

# A browser call is "starting" until Twilio first asks what to do with it. One
# still starting after this never connected (microphone refused, tab closed):
# it stops blocking a new call and is cleaned up.
STARTING = "starting"
UNCONNECTED_AFTER = timedelta(minutes=2)

TIMELINE_LIMIT = 150


class CallInProgressError(ReferralError):
    status_code = 409


class CallLimitError(ReferralError):
    status_code = 409


class CallAlreadyRecordedError(ReferralError):
    status_code = 409


# ---------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------


def text_window_open(now: datetime | None = None) -> bool:
    """True inside the hours texts may be sent (clinic time)."""
    local = (now or datetime.now(UTC)).astimezone(BUSINESS_TZ)

    return TEXT_FROM_HOUR <= local.hour < TEXT_UNTIL_HOUR


def next_business_day_at(now: datetime | None = None, hour: int = NEXT_TOUCH_HOUR) -> datetime:
    """9:00 AM (clinic time) on the next weekday after `now`."""
    local = (now or datetime.now(UTC)).astimezone(BUSINESS_TZ)
    candidate = (local + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)

    while candidate.weekday() >= 5:  # Saturday, Sunday
        candidate += timedelta(days=1)

    return candidate


def _clean(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()

    return stripped or None


# ---------------------------------------------------------------------
# What is allowed right now
# ---------------------------------------------------------------------


def _optout(db: Session, phone_e164: str | None) -> CCFpContactOptout | None:
    return db.get(CCFpContactOptout, phone_e164) if phone_e164 else None


def open_call(db: Session, referral_id: int) -> CCFpCallLog | None:
    """A call started recently and not yet given a result."""
    return db.scalar(
        select(CCFpCallLog)
        .where(
            CCFpCallLog.referral_id == referral_id,
            CCFpCallLog.result.is_(None),
            CCFpCallLog.started_at >= datetime.now(UTC) - STALE_CALL_AFTER,
            or_(
                CCFpCallLog.twilio_status.is_distinct_from(STARTING),
                CCFpCallLog.started_at >= datetime.now(UTC) - UNCONNECTED_AFTER,
            ),
        )
        .order_by(CCFpCallLog.id.desc())
        .limit(1)
    )


def call_block_reason(db: Session, card: R) -> str | None:
    """Why a new call cannot be started, or None if it can. Same rules the
    Call button and the server both use."""
    if card.status == REVIEW:
        return "Release this referral from Intake Review before calling."

    if card.status == FAX_PENDING:
        return f"All {CALL_LIMIT} calls have been made."

    if card.status not in WORKING:
        status_row = db.get(CCFpStatus, card.status)

        if status_row is not None and not status_row.is_open:
            return "This referral is closed."

        return "This referral cannot be called right now."

    if not card.phone_e164:
        return "There is no valid phone number. Add one in the booking details."

    optout = _optout(db, card.phone_e164)

    if optout is not None and optout.dnc_at is not None:
        return "This number is on the do-not-call list."

    if card.qualifying_call_count >= CALL_LIMIT:
        return f"The {CALL_LIMIT}-call limit has been reached."

    if open_call(db, card.id) is not None:
        return "A call is in progress. Record its result first."

    return None


def voice_configured() -> bool:
    """Whether click-to-call can run: Twilio credentials (the auth token also
    verifies Twilio's callbacks), the API key and TwiML App for browser calling,
    the organisation's voice number, and a public URL Twilio can reach."""
    return bool(
        voice_token_configured()
        and settings.twilio_auth_token
        and settings.twilio_voice_from_number
        and settings.public_api_base_url
    )


def dial_block_reason(call_reason: str | None) -> str | None:
    """Why the Call patient button cannot place a call: the reason the referral
    cannot be called, or click-to-call is not set up."""
    if call_reason is not None:
        return call_reason

    if not voice_configured():
        return "Click-to-call is not set up yet. Call from your phone, then record the result."

    return None


# What Twilio's report of the patient's leg suggests as the call result. Only a
# suggestion: the agent still picks the result, as voicemail can look answered.
SUGGESTED_RESULT = {
    "patient:no-answer": "no_answer",
    "patient:busy": "busy",
    "patient:failed": "failed",
}


def text_block_reason(db: Session, card: R, *, now: datetime | None = None) -> str | None:
    """Why a text cannot be sent now, or None if it can."""
    if card.status == REVIEW:
        return "Release this referral from Intake Review before texting."

    status_row = db.get(CCFpStatus, card.status)

    if status_row is not None and not status_row.is_open:
        return "This referral is closed."

    if not card.phone_e164:
        return "There is no valid phone number. Add one in the booking details."

    optout = _optout(db, card.phone_e164)

    if optout is not None and optout.dnc_at is not None:
        return "This number is on the do-not-call list."

    if optout is not None and optout.sms_opt_out_at is not None:
        return "The patient has opted out of text messages."

    if not text_window_open(now):
        return (
            f"Texts are not sent between {TEXT_UNTIL_HOUR - 12} PM and {TEXT_FROM_HOUR} AM "
            "(clinic time)."
        )

    return None


# ---------------------------------------------------------------------
# The case view
# ---------------------------------------------------------------------


def _load_card(db: Session, referral_id: int) -> R:
    card = db.get(R, referral_id)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    return card


def _call_source(call: CCFpCallLog) -> str:
    """ "twilio" for a call placed from the browser (it has a Twilio call id once
    Twilio has picked it up, and is "starting" before that), "manual" for one an
    agent logged after calling another way."""
    return "twilio" if (call.twilio_call_sid or call.twilio_status == STARTING) else "manual"


def get_case(db: Session, referral_id: int) -> CaseDetailResponse:
    card = _load_card(db, referral_id)
    fax = db.get(CCFpFax, card.fax_id)
    statuses = {s.code: s for s in db.scalars(select(CCFpStatus))}
    status_row = statuses[card.status]
    results = {
        r.code: r for r in db.scalars(select(CCFpCallResult).order_by(CCFpCallResult.sort_order))
    }

    exams = {
        e.slot: ExamChoice(
            acuity_appointment_type_id=e.acuity_appointment_type_id,
            name=e.appointment_type,
            fax_text=e.fax_text,
        )
        for e in db.scalars(select(CCFpReferralExam).where(CCFpReferralExam.referral_id == card.id))
    }

    unconnected_before = datetime.now(UTC) - UNCONNECTED_AFTER
    calls = [
        c
        for c in db.scalars(
            select(CCFpCallLog)
            .where(CCFpCallLog.referral_id == card.id)
            .order_by(CCFpCallLog.started_at.asc(), CCFpCallLog.id.asc())
        )
        # A browser call that never reached Twilio is not a call.
        if not (
            c.twilio_status == STARTING and c.result is None and c.started_at < unconnected_before
        )
    ]
    messages = list(
        db.scalars(
            select(CCFpMessageLog)
            .where(CCFpMessageLog.referral_id == card.id)
            .order_by(CCFpMessageLog.created_at.asc(), CCFpMessageLog.id.asc())
        )
    )
    history = list(
        db.scalars(
            select(CCFpReferralStatusHistory)
            .where(CCFpReferralStatusHistory.referral_id == card.id)
            .order_by(
                CCFpReferralStatusHistory.changed_at.asc(), CCFpReferralStatusHistory.id.asc()
            )
        )
    )
    edits = list(
        db.scalars(
            select(CCFpActivityLog)
            .where(
                CCFpActivityLog.referral_id == card.id,
                CCFpActivityLog.action.in_(["edit_field", "reassign", "do_not_call", "fax_back"]),
            )
            .order_by(CCFpActivityLog.occurred_at.asc(), CCFpActivityLog.id.asc())
        )
    )

    user_ids = {
        uid
        for uid in (
            [card.assigned_user_id]
            + [c.user_id for c in calls]
            + [m.user_id for m in messages]
            + [h.changed_by_user_id for h in history]
            + [e.user_id for e in edits]
        )
        if uid is not None
    }
    people = {
        row.id: row
        for row in db.execute(
            select(CCUser.id, CCUser.first_name, CCUser.last_name, CCUser.email).where(
                CCUser.id.in_(user_ids)
            )
        )
    }

    def person(user_id: int | None) -> str | None:
        row = people.get(user_id) if user_id is not None else None

        return (_full_name(row.first_name, row.last_name) or row.email) if row else None

    optout = _optout(db, card.phone_e164)
    in_progress = open_call(db, card.id)
    call_reason = call_block_reason(db, card)
    dial_reason = dial_block_reason(call_reason)
    text_reason = text_block_reason(db, card)

    case_calls = [
        CaseCall(
            id=c.id,
            started_at=c.started_at,
            user_name=person(c.user_id),
            result=c.result,
            result_label=results[c.result].label if c.result in results else None,
            counts_toward_limit=c.counts_toward_limit,
            attempt_number=c.attempt_number,
            other_reason=c.other_reason,
            notes=c.notes,
            twilio_status=c.twilio_status,
            duration_s=c.duration_s,
            source=_call_source(c),
        )
        for c in calls
    ]
    case_messages = [
        CaseMessage(
            id=m.id,
            direction=m.direction,  # type: ignore[arg-type]
            channel=m.channel,  # type: ignore[arg-type]
            body=m.body,
            status=m.status,
            status_at=m.status_at,
            created_at=m.created_at,
            user_name=person(m.user_id),
            template_code=m.template_code,
            error_message=m.error_message,
        )
        for m in messages
    ]

    timeline = _timeline(history, calls, messages, edits, statuses, results, person)

    assignee = None

    if card.assigned_user_id is not None and card.assigned_user_id in people:
        row = people[card.assigned_user_id]
        assignee = AssigneeResponse(
            user_id=row.id,
            name=_full_name(row.first_name, row.last_name) or row.email,
            initials=_initials(row.first_name, row.last_name),
        )

    return CaseDetailResponse(
        id=card.id,
        row_version=card.row_version,
        status=card.status,
        status_label=status_row.label,
        is_open=status_row.is_open,
        created_at=card.created_at,
        fields=CaseFields(
            first_name=card.first_name,
            last_name=card.last_name,
            dob=card.dob,
            phone=card.phone,
            gender=card.gender,
            email=card.email,
            physician_name=card.physician_name,
            physician_fax_no=card.physician_fax_no,
            physician_email=card.physician_email,
            address=card.address,
            reason=card.reason,
            reason_other=card.reason_other,
        ),
        clinic=(
            ClinicChoice(
                acuity_calendar_id=card.acuity_calendar_id, name=card.acuity_calendar_name or ""
            )
            if card.acuity_calendar_id is not None
            else None
        ),
        exam_1=exams.get(1),
        exam_2=exams.get(2),
        assignee=assignee,
        fax=CaseFax(
            id=fax.id,
            job_id=fax.job_id,
            page_count=fax.page_count,
            received_at=fax.received_at,
            has_pdf=fax.gcs_uri is not None,
        ),
        outreach=CaseOutreach(
            qualifying_call_count=card.qualifying_call_count,
            total_call_count=card.total_call_count,
            call_limit=CALL_LIMIT,
            last_contact_at=card.last_contact_at,
            next_action_at=card.next_action_at,
            next_action_type=card.next_action_type,
            callback_at=card.callback_at,
        ),
        phone_e164=card.phone_e164,
        sms_opted_out=bool(optout and optout.sms_opt_out_at),
        do_not_call=bool(optout and optout.dnc_at),
        can_call=call_reason is None,
        call_blocked_reason=call_reason,
        can_text=text_reason is None,
        text_blocked_reason=text_reason,
        open_call_id=in_progress.id if in_progress else None,
        open_call_source=_call_source(in_progress) if in_progress else None,
        open_call_status=in_progress.twilio_status if in_progress else None,
        suggested_result=(
            SUGGESTED_RESULT.get(in_progress.twilio_status or "") if in_progress else None
        ),
        call_service_connected=voice_configured(),
        can_dial=dial_reason is None,
        dial_blocked_reason=dial_reason,
        result_options=[
            CallResultOption(
                code=r.code,
                label=r.label,
                counts_toward_limit_default=r.counts_toward_limit_default,
            )
            for r in results.values()
        ],
        calls=case_calls,
        messages=case_messages,
        timeline=timeline,
    )


def _timeline(
    history: list[CCFpReferralStatusHistory],
    calls: list[CCFpCallLog],
    messages: list[CCFpMessageLog],
    edits: list[CCFpActivityLog],
    statuses: dict[str, CCFpStatus],
    results: dict[str, CCFpCallResult],
    person: Any,
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []

    for h in history:
        label = statuses[h.to_status].label if h.to_status in statuses else h.to_status

        if h.from_status is None:
            events.append(
                TimelineEvent(
                    at=h.changed_at,
                    kind="system",
                    title="Fax received",
                    detail=f"Placed in {label}",
                    actor=person(h.changed_by_user_id),
                )
            )
        else:
            before = statuses[h.from_status].label if h.from_status in statuses else h.from_status
            events.append(
                TimelineEvent(
                    at=h.changed_at,
                    kind="status",
                    title=f"Moved to {label}",
                    detail="; ".join(x for x in [f"From {before}", h.note] if x),
                    actor=person(h.changed_by_user_id) or "System",
                )
            )

    for number, c in enumerate(calls, start=1):
        if c.result:
            label = results[c.result].label if c.result in results else c.result
            parts = []

            if c.counts_toward_limit and c.attempt_number:
                parts.append(f"Counts as attempt {c.attempt_number} of {CALL_LIMIT}")

            parts.extend(x for x in [c.other_reason, c.notes] if x)
            events.append(
                TimelineEvent(
                    at=c.started_at,
                    kind="call",
                    title=f"Call {number}: {label}",
                    detail="; ".join(parts) or None,
                    actor=person(c.user_id),
                )
            )
        else:
            events.append(
                TimelineEvent(
                    at=c.started_at,
                    kind="call",
                    title=f"Call {number} started",
                    detail="Waiting for a result",
                    actor=person(c.user_id),
                )
            )

    for m in messages:
        snippet = m.body if len(m.body) <= 140 else m.body[:137] + "..."
        events.append(
            TimelineEvent(
                at=m.created_at,
                kind="message",
                title=("Text received" if m.direction == "inbound" else f"Text {m.status}"),
                detail=snippet,
                actor=person(m.user_id),
            )
        )

    for e in edits:
        if e.action == "edit_field":
            fields = ", ".join(sorted((e.changes or {}).keys()))
            events.append(
                TimelineEvent(
                    at=e.occurred_at,
                    kind="edit",
                    title="Details edited",
                    detail=fields or None,
                    actor=person(e.user_id),
                )
            )
        elif e.action == "reassign":
            events.append(
                TimelineEvent(
                    at=e.occurred_at,
                    kind="edit",
                    title="Reassigned",
                    actor=person(e.user_id),
                )
            )
        elif e.action == "do_not_call":
            events.append(
                TimelineEvent(
                    at=e.occurred_at,
                    kind="edit",
                    title="Marked do not call",
                    actor=person(e.user_id),
                )
            )
        elif e.action == "fax_back":
            events.append(
                TimelineEvent(
                    at=e.occurred_at,
                    kind="edit",
                    title="Faxed the referring provider",
                    detail="Unable to reach the patient",
                    actor=person(e.user_id),
                )
            )

    events.sort(key=lambda event: event.at, reverse=True)

    return events[:TIMELINE_LIMIT]


# ---------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------


def _call_error(reason: str) -> ReferralError:
    """The right error (and HTTP status) for a reason a call cannot start."""
    if reason.startswith("A call is in progress"):
        return CallInProgressError(reason)

    if "limit" in reason:
        return CallLimitError(reason)

    return InvalidMoveError(reason)


def start_call_log(
    db: Session,
    referral_id: int,
    *,
    user: CCUser,
    twilio_call_sid: str | None = None,
    from_number: str | None = None,
    source: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CCFpCallLog:
    """Creates the call row the moment a call starts (or is logged by hand).

    Locks the referral row, so two tabs cannot both start a call: only one
    call can be waiting for its result at a time. The call limit is checked
    here (no new call after the 5th qualifying one) and again when the result
    is recorded.
    """
    card = db.scalar(select(R).where(R.id == referral_id).with_for_update())

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    reason = call_block_reason(db, card)

    if reason is not None:
        raise _call_error(reason)

    call = CCFpCallLog(
        referral_id=card.id,
        user_id=user.id,
        direction="outbound",
        to_number=card.phone_e164 or "",
        from_number=from_number,
        twilio_call_sid=twilio_call_sid,
    )
    db.add(call)
    db.flush()

    log_activity(
        db,
        section="referral_case",
        action="call_start",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        details={
            "call_id": call.id,
            "source": source or ("twilio" if twilio_call_sid else "manual"),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return call


def record_call_result(
    db: Session,
    call_id: int,
    payload: CallResultRequest,
    *,
    user: CCUser,
    role_codes: set[str],
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CallResultResponse:
    """Records how a call went and moves the card accordingly, in one
    transaction with the referral row locked.

    Sets counts_toward_limit and attempt_number, bumps qualifying_call_count,
    total_call_count and last_contact_at, moves the card through move_referral
    (source call_result) following cc_fp_call_result.next_status, and on the
    5th qualifying call sends it to Fax Provider Name Pending. A 6th
    qualifying call is impossible: the check runs under the row lock, so two
    tabs cannot both slip past it.
    """
    call = db.get(CCFpCallLog, call_id)

    if call is None:
        raise ReferralNotFoundError("Call not found.")

    card = db.scalar(select(R).where(R.id == call.referral_id).with_for_update())

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    # Re-read under the lock: another tab may have just recorded this call.
    db.refresh(call)

    if call.result is not None:
        raise CallAlreadyRecordedError("This call already has a result.")

    result = db.get(CCFpCallResult, payload.result)

    if result is None:
        raise InvalidMoveError("Choose a result for the call.")

    is_other = result.code == "other"
    other_reason = _clean(payload.other_reason)
    appointment_id = _clean(payload.acuity_appointment_id)

    if is_other and other_reason is None:
        raise InvalidMoveError("Say what happened on the call.")

    if result.code == "callback_requested" and payload.callback_at is None:
        raise InvalidMoveError("Pick when the patient asked to be called back.")

    counts = bool(payload.counts_toward_limit) if is_other else result.counts_toward_limit_default

    if counts and card.qualifying_call_count >= CALL_LIMIT:
        raise CallLimitError(f"The {CALL_LIMIT}-call limit has already been reached.")

    call.result = result.code
    call.other_reason = other_reason if is_other else None
    call.notes = _clean(payload.notes)
    call.counts_toward_limit = counts
    call.updated_at = func.now()

    card.total_call_count += 1

    if counts:
        card.qualifying_call_count += 1
        call.attempt_number = card.qualifying_call_count

    card.last_contact_at = func.now()

    limit_reached = counts and card.qualifying_call_count >= CALL_LIMIT
    target: str | None = None

    if limit_reached and card.status in WORKING:
        target = FAX_PENDING
    elif result.next_status:
        target = result.next_status

    moved_to: str | None = None

    if target and target != card.status:
        try:
            # A savepoint, so a refused move leaves no half-applied changes: the
            # call result is still worth recording even if the card has since
            # been moved somewhere this result cannot follow.
            with db.begin_nested():
                move_referral(
                    db,
                    card.id,
                    target,
                    source="call_result",
                    user=user,
                    role_codes=role_codes,
                    extra=MoveExtra(
                        acuity_appointment_id=appointment_id,
                        callback_at=payload.callback_at,
                        confirmed=True,
                    ),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

            moved_to = target
        except (InvalidMoveError, MoveForbiddenError):
            # A booking that cannot be applied (e.g. that appointment is already
            # linked to another referral) is something the agent must fix, so it
            # is reported rather than recording a "booked" call on a card that
            # did not move. Other results are still worth recording.
            if result.code == "booked":
                raise

            moved_to = None

    if moved_to is None:
        card.updated_at = func.now()
        card.row_version += 1

    _set_next_action(db, card, result.code, payload)

    log_activity(
        db,
        section="referral_case",
        action="call_result",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        details={
            "call_id": call.id,
            "result": result.code,
            "counts_toward_limit": counts,
            "attempt_number": call.attempt_number,
            "moved_to": moved_to,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.flush()

    return CallResultResponse(
        call_id=call.id,
        status=card.status,
        row_version=card.row_version,
        qualifying_call_count=card.qualifying_call_count,
        counts_toward_limit=counts,
        moved_to=moved_to,
        call_limit_reached=card.qualifying_call_count >= CALL_LIMIT,
    )


def _set_next_action(db: Session, card: R, result_code: str, payload: CallResultRequest) -> None:
    """After a call: when the next touch is due (default: next business day,
    9:00 AM clinic time; the callback time when the patient asked for one).
    Cards that are finished or out of calls have nothing scheduled."""
    if card.status in WORKING:
        if result_code == "callback_requested" and payload.callback_at is not None:
            card.callback_at = payload.callback_at
            card.next_action_at = payload.callback_at
        else:
            card.callback_at = None
            card.next_action_at = payload.next_action_at or next_business_day_at()

        card.next_action_type = "call"

        return

    card.callback_at = None
    card.next_action_at = None
    card.next_action_type = None


# ---------------------------------------------------------------------
# Do not call
# ---------------------------------------------------------------------


def mark_do_not_call(
    db: Session,
    referral_id: int,
    *,
    user: CCUser,
    role_codes: set[str],
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DoNotCallResponse:
    """Blocks the number for calls and texts on every referral, and closes
    this one as declined. Needs a valid phone number."""
    card = db.scalar(select(R).where(R.id == referral_id).with_for_update())

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    if not card.phone_e164:
        raise InvalidMoveError("There is no valid phone number to block.")

    optout = db.get(CCFpContactOptout, card.phone_e164)

    if optout is None:
        optout = CCFpContactOptout(phone_e164=card.phone_e164)
        db.add(optout)

    optout.dnc_at = func.now()
    optout.source = "agent"
    optout.updated_by_user_id = user.id
    optout.updated_at = func.now()

    status_row = db.get(CCFpStatus, card.status)

    if status_row is not None and status_row.is_open and card.status != REVIEW:
        try:
            with db.begin_nested():
                move_referral(
                    db,
                    card.id,
                    DECLINED,
                    source="button",
                    user=user,
                    role_codes=role_codes,
                    extra=MoveExtra(note="Do not call"),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
        except (InvalidMoveError, MoveForbiddenError):
            pass

    card.next_action_at = None
    card.next_action_type = None
    card.callback_at = None

    log_activity(
        db,
        section="referral_case",
        action="do_not_call",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.flush()

    return DoNotCallResponse(status=card.status, row_version=card.row_version)


# ---------------------------------------------------------------------
# Search (the Referral Case page with no referral chosen)
# ---------------------------------------------------------------------


def search_referrals(db: Session, q: str | None, limit: int = 20) -> list[ReferralSearchItem]:
    """Finds referrals by patient name or phone; newest activity first. With no
    search text, the most recently updated ones."""
    query = select(R, CCFpStatus.label).join(CCFpStatus, CCFpStatus.code == R.status)
    term = (q or "").strip()

    if term:
        like = f"%{term.lower()}%"
        options = [func.lower(func.concat_ws(" ", R.first_name, R.last_name)).like(like)]
        digits = "".join(ch for ch in term if ch.isdigit())

        if len(digits) >= 3:
            options.append(R.phone_e164.like(f"%{digits}%"))
            options.append(R.phone.like(f"%{digits}%"))

        e164 = normalize_e164(term)

        if e164:
            options.append(R.phone_e164 == e164)

        query = query.where(or_(*options))

    rows = db.execute(query.order_by(R.updated_at.desc(), R.id.desc()).limit(limit)).all()

    people = {
        row.id: row
        for row in db.execute(
            select(CCUser.id, CCUser.first_name, CCUser.last_name, CCUser.email).where(
                CCUser.id.in_({c.assigned_user_id for c, _ in rows if c.assigned_user_id})
            )
        )
    }

    items = []

    for card, label in rows:
        owner = people.get(card.assigned_user_id) if card.assigned_user_id else None
        items.append(
            ReferralSearchItem(
                id=card.id,
                display_name=_full_name(card.first_name, card.last_name) or "Unknown patient",
                phone=card.phone,
                status=card.status,
                status_label=label,
                created_at=card.created_at,
                assignee=(
                    AssigneeResponse(
                        user_id=owner.id,
                        name=_full_name(owner.first_name, owner.last_name) or owner.email,
                        initials=_initials(owner.first_name, owner.last_name),
                    )
                    if owner
                    else None
                ),
            )
        )

    return items
