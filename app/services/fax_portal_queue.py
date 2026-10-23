from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpCallLog,
    CCFpCallResult,
    CCFpMessageLog,
    CCFpReferralBoard,
    CCFpReferralExam,
)
from app.schemas.fax_portal import CALL_LIMIT, CallQueueItem, CallQueueResponse
from app.services.fax_portal_referrals import CALLBACK, FOLLOW_UP, READY, WORKING

# "My Call Queue": the referrals assigned to the signed-in user that are being
# worked (Ready for Contact, Follow-up Due, Callback Requested), in the order to
# call them:
#
#   callbacks due, then patients who texted back, then overdue follow-ups, then
#   new referrals oldest first, then what is scheduled for later today.
#
# What is scheduled for a later day is left out: it appears once it is due.

R = CCFpReferralBoard

# The clinic's day decides what "later today" means.
BUSINESS_TZ = ZoneInfo("America/Chicago")

# An agent's open cards: far more than anyone works, so the page cannot grow
# without bound.
MAX_ITEMS = 500

RANK = {"callback": 0, "reply": 1, "overdue": 2, "new": 3, "later": 4}

LABELS = {"callback": "Callback", "reply": "SMS reply", "overdue": "Overdue", "new": "New"}


@dataclass
class _Entry:
    card: R
    priority: str
    # What the order within a group is decided by (earliest first).
    sort_key: datetime


def _time_label(when: datetime) -> str:
    """3 PM, 5:30 PM: the time of day in the clinic's timezone."""
    local = when.astimezone(BUSINESS_TZ)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"

    return f"{hour} {suffix}" if local.minute == 0 else f"{hour}:{local.minute:02d} {suffix}"


def _classify(card: R, now: datetime, has_reply: bool) -> _Entry | None:
    """Where a card sits in the queue, or None when it is not due today."""
    due_at = card.callback_at if card.status == CALLBACK else card.next_action_at
    # A callback with no time recorded is treated as due.
    is_due = due_at is None or due_at <= now
    received = card.created_at

    if has_reply:
        return _Entry(card, "reply", received)

    if is_due:
        if card.status == CALLBACK:
            return _Entry(card, "callback", due_at or received)

        if card.status == FOLLOW_UP or (card.status == READY and card.total_call_count > 0):
            return _Entry(card, "overdue", due_at or received)

        return _Entry(card, "new", received)

    # Scheduled: only what is due later today is shown.
    if (
        due_at is not None
        and due_at.astimezone(BUSINESS_TZ).date() == now.astimezone(BUSINESS_TZ).date()
    ):
        return _Entry(card, "later", due_at)

    return None


def _entries(db: Session, user: CCUser) -> tuple[list[_Entry], set[int]]:
    now = datetime.now(UTC)
    cards = list(
        db.scalars(
            select(R).where(R.assigned_user_id == user.id, R.status.in_(WORKING)).limit(MAX_ITEMS)
        )
    )
    ids = [c.id for c in cards]

    # A patient who texted back since we last contacted them.
    replied: set[int] = set()

    if ids:
        last_inbound = dict(
            db.execute(
                select(CCFpMessageLog.referral_id, func.max(CCFpMessageLog.created_at))
                .where(
                    CCFpMessageLog.referral_id.in_(ids),
                    CCFpMessageLog.direction == "inbound",
                )
                .group_by(CCFpMessageLog.referral_id)
            ).all()
        )

        for card in cards:
            inbound = last_inbound.get(card.id)

            if inbound is not None and (
                card.last_contact_at is None or inbound > card.last_contact_at
            ):
                replied.add(card.id)

    entries = [
        entry for card in cards if (entry := _classify(card, now, card.id in replied)) is not None
    ]
    entries.sort(key=lambda e: (RANK[e.priority], e.sort_key))

    return entries, replied


def count_due(db: Session, user: CCUser) -> int:
    """Referrals to work now: the sidebar badge. Everything but "later today"."""
    entries, _ = _entries(db, user)

    return sum(1 for e in entries if e.priority != "later")


def get_queue(db: Session, user: CCUser) -> CallQueueResponse:
    entries, replied = _entries(db, user)
    ids = [e.card.id for e in entries]

    exams: dict[int, str] = {}
    last_results: dict[int, str] = {}

    if ids:
        exams = dict(
            db.execute(
                select(CCFpReferralExam.referral_id, CCFpReferralExam.appointment_type).where(
                    CCFpReferralExam.referral_id.in_(ids), CCFpReferralExam.slot == 1
                )
            ).all()
        )
        labels = dict(db.execute(select(CCFpCallResult.code, CCFpCallResult.label)).all())

        # The latest call that has a result, per referral.
        for referral_id, result in db.execute(
            select(CCFpCallLog.referral_id, CCFpCallLog.result)
            .where(CCFpCallLog.referral_id.in_(ids), CCFpCallLog.result.is_not(None))
            .order_by(CCFpCallLog.started_at.asc(), CCFpCallLog.id.asc())
        ):
            last_results[referral_id] = labels.get(result, result)

    items: list[CallQueueItem] = []

    for entry in entries:
        card = entry.card
        number = min(card.qualifying_call_count + 1, CALL_LIMIT)
        due_at = card.callback_at if card.status == CALLBACK else card.next_action_at

        if entry.priority == "callback" and due_at is not None:
            local = due_at.astimezone(BUSINESS_TZ)
            same_day = local.date() == datetime.now(UTC).astimezone(BUSINESS_TZ).date()
            when = (
                _time_label(due_at)
                if same_day
                else f"{local:%b} {local.day}, {_time_label(due_at)}"
            )
            next_action = f"Call at {when}"
        elif entry.priority == "reply":
            next_action = f"Reply to SMS - Call {number}"
        else:
            next_action = f"Call {number}"

        label = (
            f"Due {_time_label(due_at)}"
            if entry.priority == "later" and due_at is not None
            else LABELS[entry.priority]
        )

        items.append(
            CallQueueItem(
                referral_id=card.id,
                priority=entry.priority,  # type: ignore[arg-type]
                priority_label=label,
                patient_name=" ".join(x for x in (card.first_name, card.last_name) if x)
                or "Unknown patient",
                phone=card.phone,
                phone_e164=card.phone_e164,
                exam=exams.get(card.id),
                provider=card.physician_name,
                received_at=card.created_at,
                attempts=card.qualifying_call_count,
                attempt_limit=CALL_LIMIT,
                last_result=last_results.get(card.id),
                next_action=next_action,
            )
        )

    later = sum(1 for i in items if i.priority == "later")

    return CallQueueResponse(items=items, due_now=len(items) - later, later_today=later)
