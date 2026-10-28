from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fax_portal_helpers import add_clinic_and_exam, make_card, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpReferralBoard,
    CCFpReferralStatusHistory,
)
from app.services.fax_portal_referrals import (
    InvalidMoveError,
    MoveExtra,
    MoveForbiddenError,
    ReferralNotFoundError,
    RowVersionConflictError,
    assign_referral,
    move_referral,
)

ADMIN = {"admin"}
STAFF = {"sales"}


def move(db: Session, card: CCFpReferralBoard, to: str, **kwargs):
    kwargs.setdefault("source", "button")
    kwargs.setdefault("user", None)
    kwargs.setdefault("role_codes", STAFF)

    return move_referral(db, card.id, to, **kwargs)


# ---------------------------------------------------------------------
# Review Required -> Ready for Contact / Not Referrals
# ---------------------------------------------------------------------


def test_release_needs_clinic_and_exam_1(db_session: Session) -> None:
    card = make_card(db_session, status="review_required")

    with pytest.raises(InvalidMoveError, match="clinic and Exam 1"):
        move(db_session, card, "ready_for_contact")

    assert card.status == "review_required"


def test_release_with_clinic_and_exam_moves_and_stamps(db_session: Session) -> None:
    user = make_user(db_session, "sales")
    card = make_card(db_session, status="review_required")
    add_clinic_and_exam(db_session, card)

    result = move(db_session, card, "ready_for_contact", user=user)

    assert (result.from_status, result.to_status) == ("review_required", "ready_for_contact")
    assert card.status == "ready_for_contact"
    assert card.released_at is not None
    assert card.released_by_user_id == user.id
    assert card.row_version == 2


def test_release_works_the_same_for_drag_and_button(db_session: Session) -> None:
    for source in ("drag", "button"):
        card = make_card(db_session, status="review_required")

        with pytest.raises(InvalidMoveError):
            move(db_session, card, "ready_for_contact", source=source)


def test_not_a_referral_needs_a_reason(db_session: Session) -> None:
    card = make_card(db_session, status="review_required")

    with pytest.raises(InvalidMoveError, match="why"):
        move(db_session, card, "not_referral")

    with pytest.raises(InvalidMoveError, match="why"):
        move(db_session, card, "not_referral", extra=MoveExtra(reason="   "))


def test_not_a_referral_records_who_and_why(db_session: Session) -> None:
    user = make_user(db_session, "sales")
    card = make_card(db_session, status="review_required")

    move(db_session, card, "not_referral", user=user, extra=MoveExtra(reason="Lab report"))

    assert card.status == "not_referral"
    assert card.not_referral_reason == "Lab report"
    assert card.not_referral_by_user_id == user.id
    assert card.not_referral_at is not None


def test_only_review_cards_can_become_not_a_referral(db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact")

    with pytest.raises(InvalidMoveError):
        move(db_session, card, "not_referral", extra=MoveExtra(reason="x"))


@pytest.mark.parametrize(
    "start",
    [
        "ready_for_contact",
        "follow_up_due",
        "callback_requested",
        "fax_provider_name_pending",
        "exception",
    ],
)
def test_an_open_card_can_go_back_to_review_required(db_session: Session, start: str) -> None:
    user = make_user(db_session, "sales")
    card = make_card(db_session, status=start)

    result = move(db_session, card, "review_required", user=user, source="drag")

    assert (result.from_status, result.to_status) == (start, "review_required")
    assert card.status == "review_required"
    assert card.row_version == 2

    history = db_session.scalars(
        select(CCFpReferralStatusHistory).where(CCFpReferralStatusHistory.referral_id == card.id)
    ).one()
    assert (history.from_status, history.to_status, history.changed_by_user_id) == (
        start,
        "review_required",
        user.id,
    )


def test_sending_a_card_back_forgets_the_old_review_outcome(db_session: Session) -> None:
    user = make_user(db_session, "sales")
    card = make_card(db_session, status="review_required")
    add_clinic_and_exam(db_session, card)

    move(db_session, card, "ready_for_contact", user=user)
    assert card.released_at is not None

    move(db_session, card, "review_required", user=user)

    assert card.released_at is None and card.released_by_user_id is None
    # What the reviewer chose is kept, so releasing again is one click.
    assert card.acuity_calendar_id is not None

    move(db_session, card, "ready_for_contact", user=user)
    assert card.status == "ready_for_contact"
    assert card.released_at is not None

    actions = [
        e.action
        for e in db_session.scalars(
            select(CCFpActivityLog)
            .where(CCFpActivityLog.referral_id == card.id)
            .order_by(CCFpActivityLog.id)
        )
    ]
    assert actions == ["release", "send_to_review", "release"]


def test_not_a_referral_can_be_sent_back_by_a_manager(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    card = make_card(db_session, status="review_required")
    move(db_session, card, "not_referral", extra=MoveExtra(reason="Looked like a lab report"))
    assert card.not_referral_reason is not None

    move(
        db_session,
        card,
        "review_required",
        user=admin,
        role_codes=ADMIN,
        extra=MoveExtra(note="It was a referral after all"),
    )

    assert card.status == "review_required"
    assert card.not_referral_reason is None
    assert card.not_referral_at is None and card.not_referral_by_user_id is None


@pytest.mark.parametrize(
    "closed", ["not_referral", "didnt_respond", "appointment_booked", "declined"]
)
def test_a_closed_card_goes_back_to_review_only_as_a_manager_reopen(
    db_session: Session, closed: str
) -> None:
    card = make_card(db_session, status=closed)

    with pytest.raises(MoveForbiddenError, match="manager"):
        move(db_session, card, "review_required", role_codes=STAFF, extra=MoveExtra(note="x"))

    with pytest.raises(InvalidMoveError, match="note"):
        move(db_session, card, "review_required", role_codes=ADMIN)

    move(db_session, card, "review_required", role_codes=ADMIN, extra=MoveExtra(note="why"))
    assert card.status == "review_required"


def test_a_card_cannot_be_moved_to_the_column_it_is_in(db_session: Session) -> None:
    card = make_card(db_session, status="review_required")

    with pytest.raises(InvalidMoveError, match="already"):
        move(db_session, card, "review_required")


@pytest.mark.parametrize("target", ["follow_up_due", "callback_requested"])
def test_review_cards_must_be_released_first(db_session: Session, target: str) -> None:
    card = make_card(db_session, status="review_required")

    with pytest.raises(InvalidMoveError, match="Release"):
        move(db_session, card, target)


def test_moving_to_the_same_column_is_rejected(db_session: Session) -> None:
    card = make_card(db_session, status="follow_up_due")

    with pytest.raises(InvalidMoveError, match="already"):
        move(db_session, card, "follow_up_due")


def test_unknown_status_is_rejected(db_session: Session) -> None:
    card = make_card(db_session, status="follow_up_due")

    with pytest.raises(InvalidMoveError):
        move(db_session, card, "nope")


# ---------------------------------------------------------------------
# Working columns, booking, fax provider
# ---------------------------------------------------------------------


def test_cards_move_freely_between_working_columns(db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact")
    callback_at = datetime.now(UTC) + timedelta(hours=3)

    move(db_session, card, "follow_up_due")
    move(
        db_session,
        card,
        "callback_requested",
        extra=MoveExtra(callback_at=callback_at),
    )
    move(db_session, card, "ready_for_contact")

    assert card.status == "ready_for_contact"
    assert card.callback_at == callback_at
    assert card.row_version == 4


def test_a_drag_to_booked_needs_the_acuity_appointment(db_session: Session) -> None:
    card = make_card(db_session, status="follow_up_due")

    with pytest.raises(InvalidMoveError, match="Acuity appointment"):
        move(db_session, card, "appointment_booked", source="drag")


@pytest.mark.parametrize("source", ["button", "call_result"])
def test_an_agent_can_mark_booked_without_the_appointment(db_session: Session, source: str) -> None:
    card = make_card(db_session, status="follow_up_due")

    move(db_session, card, "appointment_booked", source=source)

    assert card.status == "appointment_booked"
    assert card.acuity_appointment_id is None and card.booked_source == "agent"


def test_booking_records_appointment_and_source(db_session: Session) -> None:
    user = make_user(db_session, "sales")
    card = make_card(db_session, status="follow_up_due")

    move(
        db_session,
        card,
        "appointment_booked",
        source="drag",
        user=user,
        extra=MoveExtra(acuity_appointment_id="12345"),
    )

    assert card.acuity_appointment_id == "12345"
    assert card.booked_source == "drag"
    assert card.booked_by_user_id == user.id
    assert card.booked_at is not None


def test_an_appointment_can_only_be_linked_to_one_referral(db_session: Session) -> None:
    first = make_card(db_session, status="follow_up_due")
    second = make_card(db_session, status="follow_up_due")

    move(
        db_session,
        first,
        "appointment_booked",
        extra=MoveExtra(acuity_appointment_id="777"),
    )

    with pytest.raises(InvalidMoveError, match="already linked"):
        move(
            db_session,
            second,
            "appointment_booked",
            extra=MoveExtra(acuity_appointment_id="777"),
        )

    assert second.status == "follow_up_due"
    assert second.acuity_appointment_id is None


def test_nightly_check_can_book_without_an_appointment_id(db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact")

    move(db_session, card, "appointment_booked", source="nightly_check")

    assert card.booked_source == "nightly_check"
    assert card.acuity_appointment_id is None


def test_closed_cards_cannot_be_booked(db_session: Session) -> None:
    card = make_card(db_session, status="didnt_respond")

    with pytest.raises(InvalidMoveError, match="closed"):
        move(
            db_session,
            card,
            "appointment_booked",
            extra=MoveExtra(acuity_appointment_id="1"),
        )


@pytest.mark.parametrize("source", ["button", "drag"])
def test_manual_move_to_fax_provider_needs_confirmation(db_session: Session, source: str) -> None:
    card = make_card(db_session, status="follow_up_due")

    with pytest.raises(InvalidMoveError, match="Confirm"):
        move(db_session, card, "fax_provider_name_pending", source=source)

    move(
        db_session,
        card,
        "fax_provider_name_pending",
        source=source,
        extra=MoveExtra(confirmed=True),
    )

    assert card.status == "fax_provider_name_pending"


def test_fifth_miss_moves_to_fax_provider_without_confirmation(db_session: Session) -> None:
    card = make_card(db_session, status="follow_up_due", qualifying_call_count=5)

    move(db_session, card, "fax_provider_name_pending", source="call_result")

    assert card.status == "fax_provider_name_pending"


@pytest.mark.parametrize("start", ["review_required", "appointment_booked", "not_referral"])
def test_fax_provider_only_from_working_columns(db_session: Session, start: str) -> None:
    card = make_card(db_session, status=start)

    with pytest.raises(InvalidMoveError):
        move(
            db_session,
            card,
            "fax_provider_name_pending",
            extra=MoveExtra(confirmed=True),
        )


def test_didnt_respond_only_from_fax_provider_pending(db_session: Session) -> None:
    pending = make_card(db_session, status="fax_provider_name_pending")
    working = make_card(db_session, status="follow_up_due")

    move(db_session, pending, "didnt_respond")
    assert pending.status == "didnt_respond"

    with pytest.raises(InvalidMoveError, match="Fax Provider Name Pending"):
        move(db_session, working, "didnt_respond")


def test_call_results_can_decline_or_flag_exception(db_session: Session) -> None:
    declined = make_card(db_session, status="ready_for_contact")
    wrong_number = make_card(db_session, status="follow_up_due")

    move(db_session, declined, "declined", source="call_result")
    move(db_session, wrong_number, "exception", source="call_result")

    assert declined.status == "declined"
    assert wrong_number.status == "exception"


# ---------------------------------------------------------------------
# Reopening closed cards: managers only, with a note
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "closed", ["didnt_respond", "appointment_booked", "not_referral", "declined"]
)
def test_reopen_is_manager_only(db_session: Session, closed: str) -> None:
    card = make_card(db_session, status=closed)

    with pytest.raises(MoveForbiddenError, match="manager"):
        move(
            db_session,
            card,
            "ready_for_contact",
            role_codes=STAFF,
            extra=MoveExtra(note="mistake"),
        )

    assert card.status == closed


def test_reopen_needs_a_note(db_session: Session) -> None:
    card = make_card(db_session, status="didnt_respond")

    with pytest.raises(InvalidMoveError, match="note"):
        move(db_session, card, "ready_for_contact", role_codes=ADMIN)


def test_manager_can_reopen_with_a_note(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    card = make_card(db_session, status="didnt_respond")

    move(
        db_session,
        card,
        "follow_up_due",
        user=admin,
        role_codes=ADMIN,
        extra=MoveExtra(note="Patient called back"),
    )

    assert card.status == "follow_up_due"

    history = db_session.scalars(
        select(CCFpReferralStatusHistory).where(CCFpReferralStatusHistory.referral_id == card.id)
    ).one()
    assert history.note == "Patient called back"


# ---------------------------------------------------------------------
# Optimistic lock, history, audit
# ---------------------------------------------------------------------


def test_stale_row_version_is_a_conflict_and_changes_nothing(db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact")
    move(db_session, card, "follow_up_due")  # row_version is now 2

    with pytest.raises(RowVersionConflictError, match="changed by someone else"):
        move(db_session, card, "callback_requested", expected_row_version=1)

    assert card.status == "follow_up_due"
    assert card.row_version == 2


def test_matching_row_version_is_accepted_and_incremented(db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact")

    move(db_session, card, "follow_up_due", expected_row_version=1)

    assert card.row_version == 2


def test_missing_referral(db_session: Session) -> None:
    with pytest.raises(ReferralNotFoundError):
        move_referral(db_session, 999_999_999, "follow_up_due", source="button", user=None)


def test_move_writes_history_and_activity(db_session: Session) -> None:
    user = make_user(db_session, "sales")
    card = make_card(db_session, status="ready_for_contact")

    move(
        db_session,
        card,
        "follow_up_due",
        source="drag",
        user=user,
        ip_address="203.0.113.9",
        user_agent="pytest",
    )

    history = db_session.scalars(
        select(CCFpReferralStatusHistory).where(CCFpReferralStatusHistory.referral_id == card.id)
    ).all()
    assert [(h.from_status, h.to_status, h.source, h.changed_by_user_id) for h in history] == [
        ("ready_for_contact", "follow_up_due", "drag", user.id)
    ]

    entry = db_session.scalars(
        select(CCFpActivityLog).where(CCFpActivityLog.referral_id == card.id)
    ).one()
    assert (entry.section, entry.action, entry.user_id) == ("board", "drag", user.id)
    assert entry.changes == {"status": {"from": "ready_for_contact", "to": "follow_up_due"}}
    assert str(entry.ip_address) == "203.0.113.9"
    assert entry.user_agent == "pytest"


def test_rejected_move_writes_nothing(db_session: Session) -> None:
    card = make_card(db_session, status="review_required")

    with pytest.raises(InvalidMoveError):
        move(db_session, card, "ready_for_contact")

    assert (
        db_session.scalars(
            select(CCFpReferralStatusHistory).where(
                CCFpReferralStatusHistory.referral_id == card.id
            )
        ).all()
        == []
    )
    assert (
        db_session.scalars(
            select(CCFpActivityLog).where(CCFpActivityLog.referral_id == card.id)
        ).all()
        == []
    )


def test_release_and_not_referral_are_named_in_the_audit_log(db_session: Session) -> None:
    released = make_card(db_session, status="review_required")
    add_clinic_and_exam(db_session, released)
    rejected = make_card(db_session, status="review_required")

    move(db_session, released, "ready_for_contact")
    move(db_session, rejected, "not_referral", extra=MoveExtra(reason="Spam"))

    actions = {
        e.referral_id: e.action
        for e in db_session.scalars(
            select(CCFpActivityLog).where(
                CCFpActivityLog.referral_id.in_([released.id, rejected.id])
            )
        )
    }

    assert actions == {released.id: "release", rejected.id: "mark_not_referral"}


# ---------------------------------------------------------------------
# Manual reassignment
# ---------------------------------------------------------------------


def test_manager_can_reassign_to_an_agent(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    agent = make_user(db_session, "sales", is_agent=True)
    card = make_card(db_session, status="ready_for_contact")

    assign_referral(db_session, card.id, agent.id, user=admin, role_codes=ADMIN)

    assert card.assigned_user_id == agent.id
    assert card.assignment_source == "manual"
    assert card.assigned_at is not None
    assert card.row_version == 2

    entry = db_session.scalars(
        select(CCFpActivityLog).where(
            CCFpActivityLog.referral_id == card.id, CCFpActivityLog.action == "reassign"
        )
    ).one()
    assert entry.changes == {"assigned_user_id": {"from": None, "to": agent.id}}


def test_unassigning_clears_the_assignment(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    agent = make_user(db_session, "sales", is_agent=True)
    card = make_card(db_session, status="ready_for_contact", assigned_user_id=agent.id)

    assign_referral(db_session, card.id, None, user=admin, role_codes=ADMIN)

    assert card.assigned_user_id is None
    assert card.assignment_source is None


def test_non_manager_cannot_reassign(db_session: Session) -> None:
    staff = make_user(db_session, "sales")
    agent = make_user(db_session, "sales", is_agent=True)
    card = make_card(db_session, status="ready_for_contact")

    with pytest.raises(MoveForbiddenError):
        assign_referral(db_session, card.id, agent.id, user=staff, role_codes=STAFF)


def test_assignee_must_be_a_sales_agent(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    sonographer = make_user(db_session, "sonographer")
    card = make_card(db_session, status="ready_for_contact")

    with pytest.raises(InvalidMoveError, match="active sales agent"):
        assign_referral(db_session, card.id, sonographer.id, user=admin, role_codes=ADMIN)


def test_sales_agent_needs_no_settings_row_to_be_assigned(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    agent = make_user(db_session, "sales")
    card = make_card(db_session, status="ready_for_contact")

    assign_referral(db_session, card.id, agent.id, user=admin, role_codes=ADMIN)

    assert card.assigned_user_id == agent.id


def test_reassign_checks_row_version(db_session: Session) -> None:
    admin = make_user(db_session, "admin")
    agent = make_user(db_session, "sales", is_agent=True)
    card = make_card(db_session, status="ready_for_contact", row_version=3)

    with pytest.raises(RowVersionConflictError):
        assign_referral(
            db_session, card.id, agent.id, user=admin, role_codes=ADMIN, expected_row_version=1
        )
