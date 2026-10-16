from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import add_clinic_and_exam, auth_headers, make_card, make_user
from sqlalchemy.orm import Session

from app.models.fax_portal import CCFpCallLog, CCFpMessageLog
from app.services.fax_portal_queue import BUSINESS_TZ


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


def later_today() -> datetime:
    """23:59:30 clinic time today: still today, and not yet due."""
    local = datetime.now(UTC).astimezone(BUSINESS_TZ)

    return local.replace(hour=23, minute=59, second=30, microsecond=0).astimezone(UTC)


@pytest.fixture
def me(db_session: Session) -> Any:
    return make_user(db_session, "sales", is_agent=True)


def queue(client: TestClient, user: Any) -> dict[str, Any]:
    response = client.get("/fax-portal/queue", headers=auth_headers(client, user))

    assert response.status_code == 200, response.text

    return response.json()


def mine(db: Session, me: Any, last: str, **kwargs: Any):
    kwargs.setdefault("status", "ready_for_contact")
    kwargs.setdefault("first_name", "Pat")
    kwargs.setdefault("phone", "(214) 555-0100")
    kwargs.setdefault("phone_e164", "+12145550100")

    return make_card(db, last_name=last, assigned_user_id=me.id, **kwargs)


def test_the_queue_is_ordered_callbacks_replies_overdue_new_then_later_today(
    client: TestClient, db_session: Session, me: Any
) -> None:
    mine(db_session, me, "Later", status="follow_up_due", next_action_at=later_today())
    mine(db_session, me, "NewNewer", created_at=ago(hours=1))
    mine(db_session, me, "NewOlder", created_at=ago(hours=5))
    mine(
        db_session,
        me,
        "Overdue",
        status="follow_up_due",
        next_action_at=ago(hours=2),
        qualifying_call_count=3,
        total_call_count=3,
    )
    replied = mine(
        db_session,
        me,
        "Replied",
        status="follow_up_due",
        next_action_at=ago(hours=1),
        qualifying_call_count=2,
        total_call_count=2,
        last_contact_at=ago(hours=3),
    )
    mine(
        db_session,
        me,
        "Callback",
        status="callback_requested",
        callback_at=ago(minutes=10),
        qualifying_call_count=1,
        total_call_count=2,
    )
    db_session.add(
        CCFpMessageLog(
            referral_id=replied.id,
            direction="inbound",
            channel="sms",
            to_number="+12145550100",
            body="Friday afternoon works",
            status="received",
        )
    )
    db_session.commit()

    body = queue(client, me)

    assert [i["patient_name"] for i in body["items"]] == [
        "Pat Callback",
        "Pat Replied",
        "Pat Overdue",
        "Pat NewOlder",
        "Pat NewNewer",
        "Pat Later",
    ]
    assert [i["priority"] for i in body["items"]] == [
        "callback",
        "reply",
        "overdue",
        "new",
        "new",
        "later",
    ]
    # Everything but "later today" is to work now.
    assert (body["due_now"], body["later_today"]) == (5, 1)


def test_each_row_says_what_to_do_next(client: TestClient, db_session: Session, me: Any) -> None:
    mine(db_session, me, "New")
    mine(
        db_session,
        me,
        "Overdue",
        status="follow_up_due",
        next_action_at=ago(hours=2),
        qualifying_call_count=3,
        total_call_count=3,
    )
    replied = mine(
        db_session,
        me,
        "Replied",
        status="follow_up_due",
        next_action_at=ago(hours=1),
        qualifying_call_count=2,
        total_call_count=2,
        last_contact_at=ago(hours=3),
    )
    mine(db_session, me, "Callback", status="callback_requested", callback_at=ago(minutes=10))
    mine(
        db_session,
        me,
        "Later",
        status="follow_up_due",
        next_action_at=later_today(),
        qualifying_call_count=1,
        total_call_count=1,
    )
    db_session.add(
        CCFpMessageLog(
            referral_id=replied.id,
            direction="inbound",
            channel="sms",
            to_number="+12145550100",
            body="hi",
            status="received",
        )
    )
    db_session.commit()

    by_name = {i["patient_name"]: i for i in queue(client, me)["items"]}

    assert (by_name["Pat New"]["priority_label"], by_name["Pat New"]["next_action"]) == (
        "New",
        "Call 1",
    )
    assert (by_name["Pat Overdue"]["priority_label"], by_name["Pat Overdue"]["next_action"]) == (
        "Overdue",
        "Call 4",
    )
    assert by_name["Pat Replied"]["priority_label"] == "SMS reply"
    assert by_name["Pat Replied"]["next_action"] == "Reply to SMS - Call 3"
    assert by_name["Pat Callback"]["priority_label"] == "Callback"
    assert by_name["Pat Callback"]["next_action"].startswith("Call at ")
    # Scheduled for later today: "Due 11:59 PM", and the call it will be.
    assert by_name["Pat Later"]["priority_label"] == "Due 11:59 PM"
    assert by_name["Pat Later"]["next_action"] == "Call 2"


def test_a_row_carries_the_patient_exam_provider_attempts_and_last_result(
    client: TestClient, db_session: Session, me: Any
) -> None:
    card = mine(
        db_session,
        me,
        "Delgado",
        first_name="Maria",
        physician_name="Dr. Whitaker",
        status="follow_up_due",
        next_action_at=ago(hours=1),
        qualifying_call_count=2,
        total_call_count=2,
    )
    add_clinic_and_exam(db_session, card)
    now = datetime.now(UTC)
    db_session.add(
        CCFpCallLog(
            referral_id=card.id,
            to_number="+12145550100",
            result="busy",
            started_at=now - timedelta(days=2),
        )
    )
    db_session.add(
        CCFpCallLog(
            referral_id=card.id,
            to_number="+12145550100",
            result="voicemail",
            started_at=now - timedelta(days=1),
        )
    )
    # A call still waiting for its result is not a "last result".
    db_session.add(
        CCFpCallLog(
            referral_id=card.id, to_number="+12145550100", started_at=now - timedelta(hours=1)
        )
    )
    db_session.commit()

    [item] = queue(client, me)["items"]

    assert item["referral_id"] == card.id and item["patient_name"] == "Maria Delgado"
    assert item["phone"] == "(214) 555-0100" and item["phone_e164"] == "+12145550100"
    assert item["exam"].startswith("Exam ") and item["provider"] == "Dr. Whitaker"
    assert (item["attempts"], item["attempt_limit"]) == (2, 5)
    assert item["last_result"] == "Left voicemail"
    assert item["received_at"]


def test_a_referral_with_no_calls_has_no_last_result(
    client: TestClient, db_session: Session, me: Any
) -> None:
    mine(db_session, me, "Fresh")
    db_session.commit()

    assert queue(client, me)["items"][0]["last_result"] is None


def test_only_my_open_working_referrals_are_in_my_queue(
    client: TestClient, db_session: Session, me: Any
) -> None:
    other = make_user(db_session, "sales", is_agent=True)
    mine(db_session, me, "Mine")
    # Someone else's, not worked yet, closed, and out of calls.
    make_card(db_session, status="ready_for_contact", last_name="Theirs", assigned_user_id=other.id)
    mine(db_session, me, "InReview", status="review_required")
    mine(db_session, me, "Booked", status="appointment_booked")
    mine(db_session, me, "NoCalls", status="fax_provider_name_pending", next_action_at=ago(hours=1))
    # Scheduled for another day: it shows up once it is due.
    mine(
        db_session,
        me,
        "Tomorrow",
        status="follow_up_due",
        next_action_at=datetime.now(UTC) + timedelta(hours=30),
    )
    db_session.commit()

    assert [i["patient_name"] for i in queue(client, me)["items"]] == ["Pat Mine"]


def test_an_empty_queue_is_empty(client: TestClient, db_session: Session, me: Any) -> None:
    assert queue(client, me) == {"items": [], "due_now": 0, "later_today": 0}


def test_the_sidebar_badge_counts_what_is_to_work_now(
    client: TestClient, db_session: Session, me: Any
) -> None:
    mine(db_session, me, "New")
    mine(db_session, me, "Overdue", status="follow_up_due", next_action_at=ago(hours=1))
    mine(db_session, me, "Later", status="follow_up_due", next_action_at=later_today())
    db_session.commit()

    counts = client.get("/fax-portal/counts", headers=auth_headers(client, me)).json()

    # Not the one scheduled for later today.
    assert counts["call_queue"] == 2


def test_the_queue_needs_portal_access(client: TestClient, db_session: Session) -> None:
    sonographer = make_user(db_session, "sonographer")

    response = client.get("/fax-portal/queue", headers=auth_headers(client, sonographer))

    assert response.status_code == 403
