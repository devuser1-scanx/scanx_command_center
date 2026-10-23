from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpReferralBoard,
    CCFpReferralExam,
    CCFpReferralStatusHistory,
    CCFpStatus,
)
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_assignment import is_assignable

# The ONE place a referral's status changes (brief rule 7). Board drag, the
# buttons in Intake Review / Referral Case, call results, the webhook and the
# scheduled jobs all call move_referral(); a drag is the same action as the
# matching button, with the same checks.

REVIEW = "review_required"
READY = "ready_for_contact"
FOLLOW_UP = "follow_up_due"
CALLBACK = "callback_requested"
FAX_PENDING = "fax_provider_name_pending"
DIDNT_RESPOND = "didnt_respond"
BOOKED = "appointment_booked"
NOT_REFERRAL = "not_referral"
DECLINED = "declined"
EXCEPTION = "exception"

# Statuses an agent works day to day; cards move freely between them.
WORKING = {READY, FOLLOW_UP, CALLBACK}

# Matches cc_fp_referral_status_history.source.
SOURCES = {
    "ingest",
    "button",
    "drag",
    "call_result",
    "acuity_webhook",
    "nightly_check",
    "scheduler",
}

# Sources that may mark a referral Appointment Booked without an Acuity
# appointment id (it is linked afterwards).
ID_NOT_NEEDED_SOURCES = {"nightly_check", "call_result", "button"}

# cc_fp_referral_board.booked_source for each history source.
BOOKED_SOURCE = {
    "button": "agent",
    "call_result": "agent",
    "drag": "drag",
    "acuity_webhook": "acuity_webhook",
    "nightly_check": "nightly_check",
    "scheduler": "agent",
}

# Roles allowed to do manager-only things (reopen a closed card, reassign).
MANAGER_ROLES = {"admin"}


class ReferralError(Exception):
    """Base for errors the API turns into a clear message for the user."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReferralNotFoundError(ReferralError):
    status_code = 404


class InvalidMoveError(ReferralError):
    """The move is not allowed. The board shows the reason and snaps the
    card back."""

    status_code = 422


class MoveForbiddenError(ReferralError):
    status_code = 403


class RowVersionConflictError(ReferralError):
    """Someone else changed the card since the caller loaded it."""

    status_code = 409


@dataclass
class MoveExtra:
    """Inputs some moves need. Anything not needed is ignored."""

    reason: str | None = None  # Not a referral: why
    note: str | None = None  # reopening a closed card: why
    acuity_appointment_id: str | None = None  # Appointment Booked
    confirmed: bool = False  # manual move to Fax Provider Name Pending
    callback_at: Any = None  # Callback Requested (optional on a manual move)


@dataclass
class MoveResult:
    referral: CCFpReferralBoard
    from_status: str
    to_status: str


def move_referral(
    db: Session,
    referral_id: int,
    to_status: str,
    *,
    source: str,
    user: CCUser | None,
    role_codes: set[str] | None = None,
    expected_row_version: int | None = None,
    extra: MoveExtra | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> MoveResult:
    """Validates and applies one status change.

    Locks the referral row, checks the optimistic lock (row_version), runs the
    transition rules, then updates the card, writes
    cc_fp_referral_status_history and cc_fp_activity_log in the caller's
    transaction. The caller commits (or rolls back on ReferralError).

    user is None for the system (webhook, jobs); role_codes carries the
    acting user's roles for the manager-only reopen rule.
    """
    extra = extra or MoveExtra()

    if source not in SOURCES:
        raise ValueError(f"Unknown move source '{source}'.")

    statuses = {row.code: row for row in db.scalars(select(CCFpStatus)).all()}

    if to_status not in statuses:
        raise InvalidMoveError("That is not a valid status.")

    referral = db.scalar(
        select(CCFpReferralBoard).where(CCFpReferralBoard.id == referral_id).with_for_update()
    )

    if referral is None:
        raise ReferralNotFoundError("Referral not found.")

    if expected_row_version is not None and referral.row_version != expected_row_version:
        raise RowVersionConflictError(
            "This referral was changed by someone else. The board has been refreshed."
        )

    from_status = referral.status

    if from_status == to_status:
        raise InvalidMoveError("The referral is already in that column.")

    now = func.now()
    is_manager = bool(role_codes and role_codes & MANAGER_ROLES)
    from_open = statuses[from_status].is_open
    action = "drag" if source == "drag" else "status_change"
    changes: dict[str, Any] = {}

    # -- Transition rules ---------------------------------------------
    if to_status == REVIEW:
        # Sent back for another look: a card can need review again after it was
        # released, rejected or has been worked for a while. Open cards go back
        # freely; a closed one is a reopen (managers only, with a note).
        if not from_open:
            if not is_manager:
                raise MoveForbiddenError("Only a manager can reopen a closed referral.")

            if not (extra.note or "").strip():
                raise InvalidMoveError("Add a note explaining why this referral is reopened.")

        # The earlier review outcome no longer stands; the history keeps it.
        referral.released_at = None
        referral.released_by_user_id = None
        referral.not_referral_reason = None
        referral.not_referral_at = None
        referral.not_referral_by_user_id = None
        action = "send_to_review"

    elif from_status == REVIEW and to_status == READY:
        # Same checks as the Release to Sales button.
        has_exam_1 = db.scalar(
            select(CCFpReferralExam.id).where(
                CCFpReferralExam.referral_id == referral.id,
                CCFpReferralExam.slot == 1,
            )
        )

        if referral.acuity_calendar_id is None or has_exam_1 is None:
            raise InvalidMoveError(
                "Choose a clinic and Exam 1 in Intake Review before releasing to Sales."
            )

        referral.released_at = now
        referral.released_by_user_id = user.id if user else None
        action = "release"

    elif from_status == REVIEW and to_status == NOT_REFERRAL:
        reason = (extra.reason or "").strip()

        if not reason:
            raise InvalidMoveError("Say why this is not a referral.")

        referral.not_referral_reason = reason
        referral.not_referral_at = now
        referral.not_referral_by_user_id = user.id if user else None
        action = "mark_not_referral"
        changes["not_referral_reason"] = {"from": None, "to": reason}

    elif to_status == BOOKED:
        if not from_open:
            raise InvalidMoveError("This referral is already closed.")

        appointment_id = (extra.acuity_appointment_id or "").strip()

        # An agent marking a referral booked (from the case page or a call
        # result) is not asked for the appointment: it is linked later. Only a
        # drag on the board, which has no other trace of the booking, needs it.
        if not appointment_id and source not in ID_NOT_NEEDED_SOURCES:
            raise InvalidMoveError("Pick the Acuity appointment this patient booked.")

        if appointment_id:
            appointment_id = appointment_id[:50]

            # A booking belongs to one referral (unique index on the column).
            already_linked = db.scalar(
                select(CCFpReferralBoard.id).where(
                    CCFpReferralBoard.acuity_appointment_id == appointment_id,
                    CCFpReferralBoard.id != referral.id,
                )
            )

            if already_linked is not None:
                raise InvalidMoveError(
                    "That Acuity appointment is already linked to another referral."
                )

            referral.acuity_appointment_id = appointment_id

        referral.booked_at = now
        referral.booked_source = BOOKED_SOURCE[source]
        referral.booked_by_user_id = user.id if user else None
        action = "mark_booked" if source != "drag" else "drag"

    elif to_status == FAX_PENDING:
        if from_status not in WORKING:
            raise InvalidMoveError(
                "Only cards being worked (Ready for Contact, Follow-up Due or "
                "Callback Requested) can move to Fax Provider Name Pending."
            )

        # Automatic on the 5th qualifying call; a person must confirm.
        if source in {"button", "drag"} and not extra.confirmed:
            raise InvalidMoveError("Confirm moving this referral to Fax Provider Name Pending.")

    elif to_status == DIDNT_RESPOND:
        # Automatic on the 5th qualifying call, from the working columns;
        # otherwise only after the physician has been faxed.
        if from_status != FAX_PENDING and not (source == "call_result" and from_status in WORKING):
            raise InvalidMoveError(
                "Only referrals in Fax Provider Name Pending can move to Didn't Respond."
            )

    elif to_status == DECLINED:
        if not from_open or from_status == REVIEW:
            raise InvalidMoveError("This referral cannot be marked as declined from here.")

    elif to_status == EXCEPTION:
        if not from_open or from_status == REVIEW:
            raise InvalidMoveError("This referral cannot be moved to Exception from here.")

    elif to_status in WORKING:
        if from_status == REVIEW:
            raise InvalidMoveError(
                "Release this referral from Intake Review before moving it further."
            )

        if not from_open:
            # Closed -> open is a reopen: managers only, with a note.
            if not is_manager:
                raise MoveForbiddenError("Only a manager can reopen a closed referral.")

            if not (extra.note or "").strip():
                raise InvalidMoveError("Add a note explaining why this referral is reopened.")

        if to_status == CALLBACK and extra.callback_at is not None:
            referral.callback_at = extra.callback_at

    elif to_status == NOT_REFERRAL:
        raise InvalidMoveError("Only a fax in Review Required can be marked as not a referral.")

    else:  # pragma: no cover - every seeded status is handled above
        raise InvalidMoveError("That move is not allowed.")

    # -- Apply ---------------------------------------------------------
    referral.status = to_status
    referral.status_changed_at = now
    referral.updated_at = now
    referral.row_version = referral.row_version + 1

    changes["status"] = {"from": from_status, "to": to_status}

    db.add(
        CCFpReferralStatusHistory(
            referral_id=referral.id,
            from_status=from_status,
            to_status=to_status,
            changed_by_user_id=user.id if user else None,
            source=source,
            note=(extra.note or extra.reason or None),
        )
    )

    log_activity(
        db,
        section="board" if source == "drag" else "referral_case",
        action=action,
        user_id=user.id if user else None,
        referral_id=referral.id,
        fax_id=referral.fax_id,
        changes=changes,
        details={"source": source},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.flush()

    return MoveResult(referral=referral, from_status=from_status, to_status=to_status)


def assign_referral(
    db: Session,
    referral_id: int,
    assignee_user_id: int | None,
    *,
    user: CCUser,
    role_codes: set[str],
    expected_row_version: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CCFpReferralBoard:
    """Manual (re)assignment of a card by a manager. assignee None unassigns.

    The assignee must be an active sales or front-desk user. Sets assignment_source to
    'manual'.
    """
    if not role_codes & MANAGER_ROLES:
        raise MoveForbiddenError("Only a manager can reassign a referral.")

    referral = db.scalar(
        select(CCFpReferralBoard).where(CCFpReferralBoard.id == referral_id).with_for_update()
    )

    if referral is None:
        raise ReferralNotFoundError("Referral not found.")

    if expected_row_version is not None and referral.row_version != expected_row_version:
        raise RowVersionConflictError(
            "This referral was changed by someone else. The board has been refreshed."
        )

    if assignee_user_id is not None and not is_assignable(db, assignee_user_id):
        raise InvalidMoveError("That person is not an active sales or front-desk user.")

    previous = referral.assigned_user_id

    if previous == assignee_user_id:
        return referral

    now = func.now()

    referral.assigned_user_id = assignee_user_id
    referral.assigned_at = now if assignee_user_id is not None else None
    referral.assignment_source = "manual" if assignee_user_id is not None else None
    referral.updated_at = now
    referral.row_version = referral.row_version + 1

    log_activity(
        db,
        section="board",
        action="reassign",
        user_id=user.id,
        referral_id=referral.id,
        fax_id=referral.fax_id,
        changes={"assigned_user_id": {"from": previous, "to": assignee_user_id}},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.flush()

    return referral
