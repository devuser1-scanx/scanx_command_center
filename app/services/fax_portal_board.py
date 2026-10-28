from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.phone import normalize_e164
from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpAgent,
    CCFpCallLog,
    CCFpReferralBoard,
    CCFpReferralExam,
    CCFpStatus,
)
from app.schemas.fax_portal import (
    AgentResponse,
    AssigneeResponse,
    BoardCardResponse,
    BoardColumnResponse,
    BoardResponse,
    BoardSummaryResponse,
    FaxPortalCountsResponse,
)
from app.services.fax_portal_assignment import assignable_user_clause
from app.services.fax_portal_referrals import REVIEW

# Business day boundaries match the cc_fp_v_* analytics views.
BUSINESS_TZ = "America/Chicago"

# Guards the browser as a column grows; "visible_count" still reports the
# real total and `truncated` says more exist.
MAX_CARDS_PER_COLUMN = 100

# Commit order and now() (the transaction start time) can differ slightly, so
# a `since` poll looks back a little to avoid missing a just-committed move.
SINCE_OVERLAP = timedelta(seconds=5)

R = CCFpReferralBoard


def _initials(first: str | None, last: str | None) -> str:
    letters = [part[0] for part in (first, last) if part]

    return "".join(letters).upper() or "?"


def _full_name(first: str | None, last: str | None) -> str:
    return " ".join(part for part in (first, last) if part)


def board_changed_since(db: Session, since: datetime) -> bool:
    """Cheap poll check: has any card changed since `since`? Ignores filters,
    so a change anywhere triggers one full reload."""
    return db.scalar(select(R.id).where(R.updated_at > since - SINCE_OVERLAP).limit(1)) is not None


def _filters(user: CCUser, assignee: str, q: str | None) -> list[Any]:
    clauses: list[Any] = []

    if assignee == "me":
        clauses.append(R.assigned_user_id == user.id)
    elif assignee == "unassigned":
        clauses.append(R.assigned_user_id.is_(None))

    term = (q or "").strip()

    if term:
        like = f"%{term.lower()}%"
        name_match = func.lower(func.concat_ws(" ", R.first_name, R.last_name)).like(like)
        options = [name_match]

        digits = "".join(ch for ch in term if ch.isdigit())

        if len(digits) >= 3:
            options.append(R.phone_e164.like(f"%{digits}%"))
            options.append(R.phone.like(f"%{digits}%"))

        e164 = normalize_e164(term)

        if e164:
            options.append(R.phone_e164 == e164)

        clauses.append(or_(*options))

    return clauses


def _visible_clause() -> Any:
    """Hides cards that have sat in a finished column longer than the column's
    hide_after_days (docs/implementation-plan.md section 3). Nothing is
    deleted."""
    return or_(
        CCFpStatus.hide_after_days.is_(None),
        R.status_changed_at >= func.now() - func.make_interval(0, 0, 0, CCFpStatus.hide_after_days),
    )


def get_board(
    db: Session,
    *,
    user: CCUser,
    assignee: str = "all",
    q: str | None = None,
    show_older: set[str] | None = None,
) -> BoardResponse:
    show_older = show_older or set()
    server_time = db.scalar(select(func.now()))
    filters = _filters(user, assignee, q)

    statuses = db.scalars(
        select(CCFpStatus).where(CCFpStatus.on_board.is_(True)).order_by(CCFpStatus.board_order)
    ).all()

    # Visible / hidden counts per column in one query.
    visible_expr = _visible_clause()
    count_rows = db.execute(
        select(
            R.status,
            func.count().label("total"),
            func.count().filter(visible_expr).label("visible"),
        )
        .join(CCFpStatus, CCFpStatus.code == R.status)
        .where(*filters)
        .group_by(R.status)
    ).all()
    counts = {row.status: (row.total, row.visible) for row in count_rows}

    columns: list[BoardColumnResponse] = []
    all_cards: list[R] = []
    rows_by_status: dict[str, list[R]] = {}

    for status in statuses:
        total, visible = counts.get(status.code, (0, 0))
        showing_older = status.code in show_older

        query = (
            select(R)
            .where(R.status == status.code, *filters)
            .order_by(R.status_changed_at.desc(), R.id.desc())
            .limit(MAX_CARDS_PER_COLUMN)
        )

        if not showing_older and status.hide_after_days is not None:
            query = query.where(
                R.status_changed_at
                >= func.now() - func.make_interval(0, 0, 0, status.hide_after_days)
            )

        rows = list(db.scalars(query))
        rows_by_status[status.code] = rows
        all_cards.extend(rows)

        shown_total = total if showing_older else visible

        columns.append(
            BoardColumnResponse(
                code=status.code,
                label=status.label,
                board_order=status.board_order,
                is_open=status.is_open,
                hide_after_days=status.hide_after_days,
                visible_count=shown_total,
                hidden_count=0 if showing_older else total - visible,
                showing_older=showing_older,
                truncated=shown_total > len(rows),
                cards=[],
            )
        )

    ids = [card.id for card in all_cards]
    exams = _exam_names(db, ids)
    people = _people(db, {c.assigned_user_id for c in all_cards if c.assigned_user_id})
    attempts = _attempts(db, ids)
    open_codes = {status.code for status in statuses if status.is_open}

    for column in columns:
        column.cards = [
            _to_card(card, exams.get(card.id, {}), people, attempts.get(card.id, []), open_codes)
            for card in rows_by_status[column.code]
        ]

    return BoardResponse(
        changed=True,
        server_time=server_time,
        summary=_summary(db),
        columns=columns,
    )


def _to_card(
    card: R,
    exams: dict[int, str],
    people: dict[int, AssigneeResponse],
    attempts: list[str],
    open_codes: set[str],
) -> BoardCardResponse:
    due_times = [t for t in (card.callback_at, card.next_action_at) if t is not None]
    working = card.status in open_codes and card.status != REVIEW

    return BoardCardResponse(
        id=card.id,
        row_version=card.row_version,
        status=card.status,
        status_changed_at=card.status_changed_at,
        created_at=card.created_at,
        first_name=card.first_name,
        last_name=card.last_name,
        display_name=_full_name(card.first_name, card.last_name) or "Unknown patient",
        clinic=card.acuity_calendar_name,
        exam_1=exams.get(1),
        exam_2=exams.get(2),
        physician_name=card.physician_name,
        is_new=card.first_viewed_at is None,
        is_urgent=card.is_urgent,
        possible_duplicate=card.duplicate_of_referral_id is not None,
        assignee=people.get(card.assigned_user_id) if card.assigned_user_id else None,
        qualifying_call_count=card.qualifying_call_count,
        total_call_count=card.total_call_count,
        attempts=attempts,  # type: ignore[arg-type]
        callback_at=card.callback_at,
        next_action_at=card.next_action_at,
        due_at=min(due_times) if due_times and working else None,
        booked_at=card.booked_at,
        booked_source=card.booked_source,
    )


def _exam_names(db: Session, ids: list[int]) -> dict[int, dict[int, str]]:
    if not ids:
        return {}

    result: dict[int, dict[int, str]] = {}

    for referral_id, slot, name in db.execute(
        select(
            CCFpReferralExam.referral_id,
            CCFpReferralExam.slot,
            CCFpReferralExam.appointment_type,
        ).where(CCFpReferralExam.referral_id.in_(ids))
    ):
        result.setdefault(referral_id, {})[slot] = name

    return result


def _people(db: Session, user_ids: set[int]) -> dict[int, AssigneeResponse]:
    if not user_ids:
        return {}

    return {
        row.id: AssigneeResponse(
            user_id=row.id,
            name=_full_name(row.first_name, row.last_name) or row.email,
            initials=_initials(row.first_name, row.last_name),
        )
        for row in db.execute(
            select(CCUser.id, CCUser.first_name, CCUser.last_name, CCUser.email).where(
                CCUser.id.in_(user_ids)
            )
        )
    }


def _attempts(db: Session, ids: list[int]) -> dict[int, list[str]]:
    if not ids:
        return {}

    result: dict[int, list[str]] = {}

    for referral_id, counts in db.execute(
        select(CCFpCallLog.referral_id, CCFpCallLog.counts_toward_limit)
        .where(CCFpCallLog.referral_id.in_(ids))
        .order_by(CCFpCallLog.started_at, CCFpCallLog.id)
    ):
        result.setdefault(referral_id, []).append("miss" if counts else "other")

    return result


def _summary(db: Session) -> BoardSummaryResponse:
    """Header pills, for the whole board regardless of the current filters."""
    chicago_today = func.date(func.timezone(BUSINESS_TZ, func.now()))
    open_working = and_(
        CCFpStatus.is_open.is_(True),
        R.status != REVIEW,
        or_(R.callback_at <= func.now(), R.next_action_at <= func.now()),
    )

    row = db.execute(
        select(
            func.count()
            .filter(func.date(func.timezone(BUSINESS_TZ, R.created_at)) == chicago_today)
            .label("received_today"),
            func.count().filter(R.status == REVIEW).label("review"),
            func.count().filter(open_working).label("overdue"),
            func.count()
            .filter(
                and_(
                    R.status == "appointment_booked",
                    func.date(func.timezone(BUSINESS_TZ, R.booked_at)) == chicago_today,
                )
            )
            .label("booked_today"),
            func.count().filter(R.status == "fax_provider_name_pending").label("fax_provider"),
        )
        .select_from(R)
        .join(CCFpStatus, CCFpStatus.code == R.status)
    ).one()

    return BoardSummaryResponse(
        received_today=row.received_today,
        review=row.review,
        overdue=row.overdue,
        booked_today=row.booked_today,
        fax_provider=row.fax_provider,
    )


def get_counts(db: Session, user: CCUser) -> FaxPortalCountsResponse:
    """Sidebar badges: what is in my call queue to work now (callbacks due,
    replies, overdue follow-ups, new referrals), and cards waiting in Intake
    Review."""
    # Imported here: the queue module is independent of this one.
    from app.services.fax_portal_queue import count_due

    intake_review = db.scalar(select(func.count()).select_from(R).where(R.status == REVIEW))

    return FaxPortalCountsResponse(call_queue=count_due(db, user), intake_review=intake_review or 0)


def list_agents(db: Session) -> list[AgentResponse]:
    """People referrals can be assigned to (active sales agents),
    for the assignee filter and reassign. accepting_new comes from the
    person's cc_fp_agent settings row when they have one."""
    return [
        AgentResponse(
            user_id=row.id,
            name=_full_name(row.first_name, row.last_name) or row.email,
            initials=_initials(row.first_name, row.last_name),
            accepting_new=row.accepting_new is not False,
        )
        for row in db.execute(
            select(
                CCUser.id,
                CCUser.first_name,
                CCUser.last_name,
                CCUser.email,
                CCFpAgent.accepting_new,
            )
            .outerjoin(CCFpAgent, CCFpAgent.user_id == CCUser.id)
            .where(assignable_user_clause())
            .order_by(CCUser.first_name, CCUser.last_name)
        )
    ]


__all__ = [
    "BoardResponse",
    "board_changed_since",
    "get_board",
    "get_counts",
    "list_agents",
]
