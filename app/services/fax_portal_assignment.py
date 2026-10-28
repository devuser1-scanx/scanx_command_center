from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.auth import CCRole, CCUser, CCUserRole
from app.models.fax_portal import CCFpAgent, CCFpReferralBoard, CCFpStatus

# Who can be assigned referrals: every active user holding one of these roles.
# Role membership is the only thing that makes someone assignable; cc_fp_agent
# just holds per-person settings (pause, open-card cap) and the round-robin
# timestamp, and a row is created for a user the first time it is needed.
ASSIGNABLE_ROLES = frozenset({"sales"})


def assignable_user_clause() -> Any:
    """SQL condition on cc_users: active and holds an assignable role."""
    has_role = (
        select(CCUserRole.id)
        .join(CCRole, CCRole.id == CCUserRole.role_id)
        .where(
            CCUserRole.user_id == CCUser.id,
            CCRole.code.in_(ASSIGNABLE_ROLES),
            CCRole.is_active.is_(True),
        )
        .correlate(CCUser)
        .exists()
    )

    return and_(CCUser.is_active.is_(True), has_role)


def is_assignable(db: Session, user_id: int) -> bool:
    return (
        db.scalar(select(CCUser.id).where(CCUser.id == user_id, assignable_user_clause()))
        is not None
    )


def ensure_agent_rows(db: Session) -> None:
    """Gives every assignable user a cc_fp_agent settings row (idempotent).

    Done before picking so the row can be locked FOR UPDATE; new role holders
    start accepting new cards with no cap, never-assigned first in line.
    """
    db.execute(
        pg_insert(CCFpAgent)
        .from_select(["user_id"], select(CCUser.id).where(assignable_user_clause()))
        .on_conflict_do_nothing(index_elements=["user_id"])
    )


def assign_new_referral(db: Session, referral: CCFpReferralBoard) -> int | None:
    """Auto-assigns a freshly created card
    (docs/implementation-plan.md section 2).

    Among active users with the sales role whose cc_fp_agent
    row is accepting new cards and is below max_open, picks the one with the
    fewest open cards (cards whose status has is_open = true); ties go to
    whoever was assigned longest ago (never-assigned first), which gives plain
    round-robin when loads are equal. The chosen row is locked FOR UPDATE SKIP
    LOCKED so two faxes arriving together cannot both pick the same person.

    Must run in the same transaction that creates the card. The caller must
    flush the session first so cards already created in this transaction
    (e.g. other patients on the same fax) count towards each person's load.

    Returns the assigned user id, or None when nobody is eligible - the card
    then stays unassigned for a manager to assign by hand.
    """
    ensure_agent_rows(db)

    open_cards = (
        select(func.count())
        .select_from(CCFpReferralBoard)
        .join(CCFpStatus, CCFpStatus.code == CCFpReferralBoard.status)
        .where(
            CCFpReferralBoard.assigned_user_id == CCFpAgent.user_id,
            CCFpStatus.is_open.is_(True),
        )
        .correlate(CCFpAgent)
        .scalar_subquery()
    )

    agent = db.execute(
        select(CCFpAgent)
        .join(CCUser, CCUser.id == CCFpAgent.user_id)
        .where(
            CCFpAgent.accepting_new.is_(True),
            assignable_user_clause(),
            or_(CCFpAgent.max_open.is_(None), open_cards < CCFpAgent.max_open),
        )
        .order_by(
            open_cards.asc(),
            CCFpAgent.last_assigned_at.asc().nulls_first(),
            CCFpAgent.user_id.asc(),
        )
        .limit(1)
        .with_for_update(of=CCFpAgent, skip_locked=True)
    ).scalar_one_or_none()

    if agent is None:
        return None

    now = func.now()

    referral.assigned_user_id = agent.user_id
    referral.assigned_at = now
    referral.assignment_source = "auto"
    agent.last_assigned_at = now

    db.flush()

    return agent.user_id
