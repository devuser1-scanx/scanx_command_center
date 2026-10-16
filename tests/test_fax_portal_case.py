from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import add_clinic_and_exam, auth_headers, make_card, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpCallLog,
    CCFpContactOptout,
    CCFpReferralBoard,
    CCFpReferralStatusHistory,
)
from app.services import fax_portal_case
from app.services.fax_portal_case import (
    next_business_day_at,
    text_window_open,
)

CHICAGO = ZoneInfo("America/Chicago")
PHONE = "+12145550187"


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "front_desk")


def worked_card(db: Session, **kwargs: Any) -> CCFpReferralBoard:
    kwargs.setdefault("status", "ready_for_contact")
    kwargs.setdefault("phone", "(214) 555-0187")
    kwargs.setdefault("phone_e164", PHONE)
    card = make_card(db, **kwargs)
    db.commit()

    return card


def case(client: TestClient, headers: dict[str, str], card_id: int) -> dict[str, Any]:
    response = client.get(f"/fax-portal/referrals/{card_id}/case", headers=headers)

    assert response.status_code == 200, response.text

    return response.json()


def start_call(client: TestClient, headers: dict[str, str], card_id: int):
    return client.post(f"/fax-portal/referrals/{card_id}/calls", headers=headers)


def record(client: TestClient, headers: dict[str, str], call_id: int, **body: Any):
    return client.post(f"/fax-portal/calls/{call_id}/result", json=body, headers=headers)


def call_and_record(client: TestClient, headers: dict[str, str], card_id: int, **body: Any):
    started = start_call(client, headers, card_id)

    assert started.status_code == 200, started.text

    return record(client, headers, started.json()["call_id"], **body)


# ---------------------------------------------------------------------
# Time rules
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now_chicago", "expected_chicago"),
    [
        # Wed afternoon -> Thu 9 AM
        (
            datetime(2026, 10, 14, 15, 0, tzinfo=CHICAGO),
            datetime(2026, 10, 15, 9, 0, tzinfo=CHICAGO),
        ),
        # Fri evening -> Mon 9 AM (weekend skipped)
        (
            datetime(2026, 10, 16, 18, 30, tzinfo=CHICAGO),
            datetime(2026, 10, 19, 9, 0, tzinfo=CHICAGO),
        ),
        # Sat morning -> Mon 9 AM
        (
            datetime(2026, 10, 17, 8, 0, tzinfo=CHICAGO),
            datetime(2026, 10, 19, 9, 0, tzinfo=CHICAGO),
        ),
        # Sun -> Mon 9 AM
        (
            datetime(2026, 10, 18, 12, 0, tzinfo=CHICAGO),
            datetime(2026, 10, 19, 9, 0, tzinfo=CHICAGO),
        ),
    ],
)
def test_next_touch_is_the_next_business_day_at_nine(
    now_chicago: datetime, expected_chicago: datetime
) -> None:
    result = next_business_day_at(now_chicago.astimezone(UTC))

    assert result == expected_chicago
    assert result.tzinfo is not None


def test_next_touch_uses_clinic_time_not_utc() -> None:
    # 9 PM Chicago on a Thursday is already Friday in UTC: still "next day" is Friday 9 AM.
    thursday_night = datetime(2026, 10, 15, 21, 0, tzinfo=CHICAGO).astimezone(UTC)

    assert next_business_day_at(thursday_night) == datetime(2026, 10, 16, 9, 0, tzinfo=CHICAGO)


@pytest.mark.parametrize(
    ("hour", "expected"), [(7, False), (8, True), (14, True), (20, True), (21, False), (23, False)]
)
def test_text_window_is_eight_to_nine_clinic_time(hour: int, expected: bool) -> None:
    assert text_window_open(datetime(2026, 10, 14, hour, 30, tzinfo=CHICAGO)) is expected


# ---------------------------------------------------------------------
# The case view
# ---------------------------------------------------------------------


def test_case_has_the_booking_form_fields_and_outreach(
    client: TestClient,
    db_session: Session,
    staff: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whether click-to-call is set up depends on the environment: not here.
    monkeypatch.setattr(fax_portal_case.settings, "twilio_twiml_app_sid", None)
    card = make_card(
        db_session,
        first_name="Maria",
        last_name="Delgado",
        phone="(214) 555-0187",
        phone_e164=PHONE,
        email="maria@example.com",
        physician_name="Dr. Whitaker",
        physician_fax_no="9725550144",
        reason="Abdominal pain",
        status="follow_up_due",
        qualifying_call_count=2,
        total_call_count=3,
    )
    add_clinic_and_exam(db_session, card)
    db_session.commit()

    body = case(client, auth_headers(client, staff), card.id)

    assert body["status"] == "follow_up_due"
    assert body["status_label"] == "Follow-up Due"
    assert body["is_open"] is True
    assert body["fields"]["first_name"] == "Maria"
    assert body["fields"]["reason"] == "Abdominal pain"
    assert body["fields"]["physician_fax_no"] == "9725550144"
    assert body["clinic"]["name"].startswith("Clinic")
    assert body["exam_1"]["name"].startswith("Exam")
    assert body["outreach"]["qualifying_call_count"] == 2
    assert body["outreach"]["call_limit"] == 5
    assert body["fax"]["id"] == card.fax_id
    assert [o["code"] for o in body["result_options"]] == [
        "booked",
        "no_answer",
        "voicemail",
        "busy",
        "failed",
        "callback_requested",
        "declined",
        "wrong_number",
        "other",
    ]
    assert body["call_service_connected"] is False


def test_case_for_a_missing_referral_is_404(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    response = client.get(
        "/fax-portal/referrals/99999999/case", headers=auth_headers(client, staff)
    )

    assert response.status_code == 404


def test_case_endpoints_need_access(client: TestClient, db_session: Session) -> None:
    card = worked_card(db_session)
    sono = auth_headers(client, make_user(db_session, "sonographer"))

    for method, path in (
        ("get", f"/fax-portal/referrals/{card.id}/case"),
        ("patch", f"/fax-portal/referrals/{card.id}/case"),
        ("post", f"/fax-portal/referrals/{card.id}/calls"),
        ("post", "/fax-portal/calls/1/result"),
        ("post", f"/fax-portal/referrals/{card.id}/do-not-call"),
        ("get", f"/fax-portal/referrals/{card.id}/message-templates"),
        ("post", f"/fax-portal/referrals/{card.id}/messages"),
        ("get", f"/fax-portal/referrals/{card.id}/booking"),
        ("get", "/fax-portal/referrals/search"),
    ):
        assert getattr(client, method)(path).status_code == 401, path
        assert getattr(client, method)(path, headers=sono).status_code == 403, path


@pytest.mark.parametrize(
    ("status", "reason_part"),
    [
        ("review_required", "Release"),
        ("fax_provider_name_pending", "calls have been made"),
        ("appointment_booked", "closed"),
        ("didnt_respond", "closed"),
        ("exception", "cannot be called"),
    ],
)
def test_calls_are_blocked_outside_the_working_columns(
    client: TestClient, db_session: Session, staff: Any, status: str, reason_part: str
) -> None:
    card = worked_card(db_session, status=status)
    headers = auth_headers(client, staff)

    body = case(client, headers, card.id)

    assert body["can_call"] is False
    assert reason_part in body["call_blocked_reason"]
    assert start_call(client, headers, card.id).status_code == 422


def test_a_card_in_a_working_column_can_be_called(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    for status in ("ready_for_contact", "follow_up_due", "callback_requested"):
        card = worked_card(db_session, status=status)
        body = case(client, auth_headers(client, staff), card.id)

        assert (body["can_call"], body["call_blocked_reason"]) == (True, None), status


def test_no_phone_blocks_calls(client: TestClient, db_session: Session, staff: Any) -> None:
    card = worked_card(db_session, phone=None, phone_e164=None)
    headers = auth_headers(client, staff)

    assert "phone number" in case(client, headers, card.id)["call_blocked_reason"]
    assert start_call(client, headers, card.id).status_code == 422


def test_the_timeline_merges_history_calls_edits_and_texts(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="ready_for_contact")
    headers = auth_headers(client, staff)

    call_and_record(client, headers, card.id, result="no_answer")
    client.patch(
        f"/fax-portal/referrals/{card.id}/case",
        json={
            "row_version": case(client, headers, card.id)["row_version"],
            "email": "x@example.com",
        },
        headers=headers,
    )

    body = case(client, headers, card.id)
    titles = [(e["kind"], e["title"]) for e in body["timeline"]]

    assert ("call", "Call 1: No answer") in titles
    assert ("status", "Moved to Follow-up Due") in titles
    assert ("edit", "Details edited") in titles
    times = [e["at"] for e in body["timeline"]]
    assert times == sorted(times, reverse=True)
    edit = next(e for e in body["timeline"] if e["kind"] == "edit")
    # Only the names of the fields, never their values.
    assert edit["detail"] == "email"
    assert "x@example.com" not in str(body["timeline"])


# ---------------------------------------------------------------------
# Editing the booking details
# ---------------------------------------------------------------------


def test_edits_in_the_case_are_audited_as_referral_case(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="follow_up_due", first_name="Mari")
    headers = auth_headers(client, staff)

    response = client.patch(
        f"/fax-portal/referrals/{card.id}/case",
        json={
            "row_version": 1,
            "first_name": "Maria",
            "email": "maria@example.com",
            "gender": "Female",
            "reason_other": "",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["changed_fields"] == ["email", "first_name", "gender"]

    entry = db_session.scalars(
        select(CCFpActivityLog).where(
            CCFpActivityLog.referral_id == card.id, CCFpActivityLog.action == "edit_field"
        )
    ).one()
    assert entry.section == "referral_case"
    assert entry.changes["first_name"] == {"from": "Mari", "to": "Maria"}


@pytest.mark.parametrize("status", ["appointment_booked", "didnt_respond", "not_referral"])
def test_closed_referrals_cannot_be_edited(
    client: TestClient, db_session: Session, staff: Any, status: str
) -> None:
    card = worked_card(db_session, status=status)

    response = client.patch(
        f"/fax-portal/referrals/{card.id}/case",
        json={"row_version": 1, "first_name": "X"},
        headers=auth_headers(client, staff),
    )

    assert response.status_code == 422
    assert "closed" in response.json()["detail"]


def test_a_stale_case_edit_is_a_conflict(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, row_version=3)

    response = client.patch(
        f"/fax-portal/referrals/{card.id}/case",
        json={"row_version": 2, "first_name": "X"},
        headers=auth_headers(client, staff),
    )

    assert response.status_code == 409


def test_intake_edits_are_still_limited_to_review_required(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="follow_up_due")

    response = client.patch(
        f"/fax-portal/referrals/{card.id}/intake",
        json={"row_version": 1, "first_name": "X"},
        headers=auth_headers(client, staff),
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# Starting calls
# ---------------------------------------------------------------------


def test_starting_a_call_creates_the_row_straight_away(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)

    response = start_call(client, headers, card.id)

    assert response.status_code == 200
    call = db_session.get(CCFpCallLog, response.json()["call_id"])
    assert (call.referral_id, call.user_id, call.to_number, call.result) == (
        card.id,
        staff.id,
        PHONE,
        None,
    )
    body = case(client, headers, card.id)
    assert body["open_call_id"] == call.id
    assert body["can_call"] is False
    assert "in progress" in body["call_blocked_reason"]
    # Nothing counts until a result is recorded.
    assert body["outreach"]["qualifying_call_count"] == 0


def test_only_one_call_can_wait_for_its_result(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)

    assert start_call(client, headers, card.id).status_code == 200
    second = start_call(client, headers, card.id)

    assert second.status_code == 409
    assert "in progress" in second.json()["detail"]
    assert (
        len(db_session.scalars(select(CCFpCallLog).where(CCFpCallLog.referral_id == card.id)).all())
        == 1
    )


def test_an_abandoned_call_stops_blocking_after_two_hours(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    old = start_call(client, headers, card.id).json()["call_id"]
    db_session.get(CCFpCallLog, old).started_at = datetime.now(UTC) - timedelta(hours=3)
    db_session.commit()

    assert start_call(client, headers, card.id).status_code == 200


def test_the_call_limit_blocks_starting_a_sixth_call(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, qualifying_call_count=5)
    headers = auth_headers(client, staff)

    body = case(client, headers, card.id)
    response = start_call(client, headers, card.id)

    assert body["can_call"] is False
    assert "limit" in body["call_blocked_reason"]
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Recording results
# ---------------------------------------------------------------------


def test_no_answer_counts_and_moves_to_follow_up_with_the_next_touch(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)

    response = call_and_record(client, headers, card.id, result="no_answer")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["moved_to"], body["qualifying_call_count"]) == (
        "follow_up_due",
        "follow_up_due",
        1,
    )
    assert body["counts_toward_limit"] is True and body["call_limit_reached"] is False

    db_session.refresh(card)
    assert (card.total_call_count, card.qualifying_call_count) == (1, 1)
    assert card.last_contact_at is not None
    assert card.next_action_type == "call"
    nxt = card.next_action_at.astimezone(CHICAGO)
    assert (nxt.hour, nxt.minute) == (9, 0) and nxt.weekday() < 5 and nxt > datetime.now(UTC)

    call = db_session.scalars(select(CCFpCallLog).where(CCFpCallLog.referral_id == card.id)).one()
    assert (call.result, call.counts_toward_limit, call.attempt_number) == ("no_answer", True, 1)

    history = db_session.scalars(
        select(CCFpReferralStatusHistory).where(CCFpReferralStatusHistory.referral_id == card.id)
    ).one()
    assert (history.source, history.to_status, history.changed_by_user_id) == (
        "call_result",
        "follow_up_due",
        staff.id,
    )


@pytest.mark.parametrize("result", ["voicemail", "busy", "failed"])
def test_other_unsuccessful_results_count_too(
    client: TestClient, db_session: Session, staff: Any, result: str
) -> None:
    card = worked_card(db_session)

    body = call_and_record(client, auth_headers(client, staff), card.id, result=result).json()

    assert (body["counts_toward_limit"], body["qualifying_call_count"], body["status"]) == (
        True,
        1,
        "follow_up_due",
    )


def test_a_second_miss_in_follow_up_stays_there_and_counts(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)

    call_and_record(client, headers, card.id, result="no_answer")
    version_before = case(client, headers, card.id)["row_version"]
    body = call_and_record(client, headers, card.id, result="voicemail").json()

    assert (body["status"], body["moved_to"], body["qualifying_call_count"]) == (
        "follow_up_due",
        None,
        2,
    )
    # No column change, but the card still changed: others see it on the next poll.
    assert body["row_version"] == version_before + 1
    attempts = db_session.scalars(
        select(CCFpCallLog.attempt_number)
        .where(CCFpCallLog.referral_id == card.id)
        .order_by(CCFpCallLog.id)
    ).all()
    assert attempts == [1, 2]


def test_the_fifth_qualifying_miss_moves_the_card_to_fax_provider_name_pending(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(
        db_session, status="follow_up_due", qualifying_call_count=4, total_call_count=4
    )
    headers = auth_headers(client, staff)

    body = call_and_record(client, headers, card.id, result="no_answer").json()

    assert body["status"] == "fax_provider_name_pending"
    assert body["moved_to"] == "fax_provider_name_pending"
    assert (body["qualifying_call_count"], body["call_limit_reached"]) == (5, True)
    db_session.refresh(card)
    # Out of calls: nothing is scheduled any more.
    assert (card.next_action_at, card.next_action_type, card.callback_at) == (None, None, None)

    after = case(client, headers, card.id)
    assert after["can_call"] is False
    assert start_call(client, headers, card.id).status_code == 422


def test_a_sixth_qualifying_call_is_impossible_even_with_calls_already_open(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    """Two tabs each started a call at 4 of 5. Both results are recorded; the
    second one cannot push the count to 6."""
    card = worked_card(db_session, status="follow_up_due", qualifying_call_count=4)
    first = CCFpCallLog(referral_id=card.id, user_id=staff.id, to_number=PHONE)
    second = CCFpCallLog(referral_id=card.id, user_id=staff.id, to_number=PHONE)
    db_session.add_all([first, second])
    db_session.commit()
    headers = auth_headers(client, staff)

    one = record(client, headers, first.id, result="no_answer")
    two = record(client, headers, second.id, result="no_answer")

    assert one.status_code == 200 and one.json()["qualifying_call_count"] == 5
    assert two.status_code == 409
    assert "limit" in two.json()["detail"]
    db_session.refresh(card)
    assert card.qualifying_call_count == 5  # never 6 (the CHECK constraint is only a backstop)
    db_session.refresh(second)
    assert second.result is None  # the refused result was not half-recorded


def test_a_non_counting_result_is_still_recorded_at_the_limit(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="follow_up_due", qualifying_call_count=5)
    call = CCFpCallLog(referral_id=card.id, user_id=staff.id, to_number=PHONE)
    db_session.add(call)
    db_session.commit()

    response = record(
        client,
        auth_headers(client, staff),
        call.id,
        result="callback_requested",
        callback_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )

    assert response.status_code == 200
    assert response.json()["qualifying_call_count"] == 5


def test_a_result_can_only_be_recorded_once(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    assert record(client, headers, call_id, result="no_answer").status_code == 200
    again = record(client, headers, call_id, result="voicemail")

    assert again.status_code == 409
    assert "already has a result" in again.json()["detail"]
    assert case(client, headers, card.id)["outreach"]["qualifying_call_count"] == 1


def test_callback_needs_a_time_and_does_not_count(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    assert record(client, headers, call_id, result="callback_requested").status_code == 422

    when = datetime.now(UTC) + timedelta(hours=5)
    body = record(
        client, headers, call_id, result="callback_requested", callback_at=when.isoformat()
    ).json()

    assert (body["status"], body["qualifying_call_count"], body["counts_toward_limit"]) == (
        "callback_requested",
        0,
        False,
    )
    db_session.refresh(card)
    assert card.callback_at == when and card.next_action_at == when
    assert card.total_call_count == 1


def test_a_later_call_clears_the_callback(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="callback_requested", callback_at=datetime.now(UTC))
    headers = auth_headers(client, staff)

    call_and_record(client, headers, card.id, result="no_answer")

    db_session.refresh(card)
    assert card.callback_at is None
    assert card.status == "follow_up_due"


def test_declined_closes_the_card_and_clears_the_schedule(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, next_action_at=datetime.now(UTC))

    body = call_and_record(client, auth_headers(client, staff), card.id, result="declined").json()

    assert body["status"] == "declined"
    db_session.refresh(card)
    assert (card.next_action_at, card.next_action_type) == (None, None)


def test_wrong_number_goes_to_exception(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)

    body = call_and_record(
        client, auth_headers(client, staff), card.id, result="wrong_number"
    ).json()

    assert (body["status"], body["counts_toward_limit"]) == ("exception", False)


def test_booked_is_recorded_without_asking_for_the_appointment(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    response = record(client, headers, call_id, result="booked")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "appointment_booked"
    db_session.refresh(card)
    # The appointment is linked afterwards, not typed in by the agent.
    assert (card.acuity_appointment_id, card.booked_source) == (None, "agent")
    assert card.booked_by_user_id == staff.id


def test_booked_still_records_an_appointment_when_one_is_given(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    body = record(client, headers, call_id, result="booked", acuity_appointment_id="APPT-1").json()

    assert body["status"] == "appointment_booked"
    db_session.refresh(card)
    assert (card.acuity_appointment_id, card.booked_source, card.booked_by_user_id) == (
        "APPT-1",
        "agent",
        staff.id,
    )
    assert card.next_action_at is None


def test_booking_an_appointment_already_linked_elsewhere_is_refused_not_half_recorded(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    make_card(db_session, status="ready_for_contact", acuity_appointment_id="TAKEN")
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    response = record(client, headers, call_id, result="booked", acuity_appointment_id="TAKEN")

    assert response.status_code == 422
    assert "already linked" in response.json()["detail"]
    db_session.refresh(card)
    assert card.status == "ready_for_contact"
    assert db_session.get(CCFpCallLog, call_id).result is None  # still waiting for a result


def test_other_needs_a_reason_and_lets_the_agent_choose_whether_it_counts(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    assert record(client, headers, call_id, result="other").status_code == 422
    assert record(client, headers, call_id, result="other", other_reason="  ").status_code == 422

    body = record(
        client,
        headers,
        call_id,
        result="other",
        other_reason="Traveling until Oct 20",
        counts_toward_limit=False,
    ).json()

    assert (body["counts_toward_limit"], body["moved_to"], body["status"]) == (
        False,
        None,
        "ready_for_contact",
    )
    call = db_session.get(CCFpCallLog, call_id)
    assert call.other_reason == "Traveling until Oct 20"

    second_id = start_call(client, headers, card.id).json()["call_id"]
    counted = record(
        client, headers, second_id, result="other", other_reason="Hung up", counts_toward_limit=True
    ).json()
    assert counted["qualifying_call_count"] == 1


def test_an_other_that_counts_can_be_the_fifth_call(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="follow_up_due", qualifying_call_count=4)

    body = call_and_record(
        client,
        auth_headers(client, staff),
        card.id,
        result="other",
        other_reason="Phone disconnected",
        counts_toward_limit=True,
    ).json()

    assert body["status"] == "fax_provider_name_pending"


def test_only_other_can_override_whether_a_call_counts(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)

    body = call_and_record(
        client, auth_headers(client, staff), card.id, result="no_answer", counts_toward_limit=False
    ).json()

    assert body["counts_toward_limit"] is True  # the default for "no answer" stands


def test_an_unknown_result_is_refused(client: TestClient, db_session: Session, staff: Any) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]

    assert record(client, headers, call_id, result="nonsense").status_code == 422
    assert record(client, headers, 99999999, result="no_answer").status_code == 404


def test_a_result_after_the_card_was_dragged_elsewhere_is_still_recorded(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    headers = auth_headers(client, staff)
    call_id = start_call(client, headers, card.id).json()["call_id"]
    # Meanwhile the card was marked as booked (closed) by someone else.
    card.status = "appointment_booked"
    db_session.commit()

    response = record(client, headers, call_id, result="no_answer")

    assert response.status_code == 200
    assert response.json()["moved_to"] is None
    assert response.json()["status"] == "appointment_booked"
    assert db_session.get(CCFpCallLog, call_id).result == "no_answer"


def test_results_are_audited_without_free_text(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)

    call_and_record(
        client,
        auth_headers(client, staff),
        card.id,
        result="other",
        other_reason="Patient is travelling to Boston",
        counts_toward_limit=True,
    )

    entries = db_session.scalars(
        select(CCFpActivityLog).where(CCFpActivityLog.referral_id == card.id)
    ).all()
    assert {"call_start", "call_result"} <= {e.action for e in entries}
    assert "Boston" not in str([e.details for e in entries] + [e.changes for e in entries])


# ---------------------------------------------------------------------
# Do not call
# ---------------------------------------------------------------------


def test_do_not_call_blocks_the_number_everywhere_and_closes_the_card(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session)
    other = worked_card(db_session, status="follow_up_due")  # same phone, another referral
    headers = auth_headers(client, staff)

    response = client.post(f"/fax-portal/referrals/{card.id}/do-not-call", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "declined"
    optout = db_session.get(CCFpContactOptout, PHONE)
    assert optout.dnc_at is not None and optout.source == "agent"

    blocked = case(client, headers, other.id)
    assert blocked["do_not_call"] is True
    assert blocked["can_call"] is False and "do-not-call" in blocked["call_blocked_reason"]
    assert blocked["can_text"] is False
    assert start_call(client, headers, other.id).status_code == 422
    assert any(
        e["title"] == "Marked do not call" for e in case(client, headers, card.id)["timeline"]
    )


def test_do_not_call_on_a_card_still_in_review_only_blocks_the_number(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, status="review_required")

    response = client.post(
        f"/fax-portal/referrals/{card.id}/do-not-call", headers=auth_headers(client, staff)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "review_required"
    assert db_session.get(CCFpContactOptout, PHONE).dnc_at is not None


def test_do_not_call_needs_a_valid_number(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = worked_card(db_session, phone=None, phone_e164=None)

    response = client.post(
        f"/fax-portal/referrals/{card.id}/do-not-call", headers=auth_headers(client, staff)
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------


def test_search_finds_referrals_by_name_or_phone(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    maria = worked_card(db_session, first_name="Maria", last_name="Delgado")
    worked_card(
        db_session,
        first_name="Kevin",
        last_name="Liu",
        phone="(972) 555-0162",
        phone_e164="+19725550162",
    )
    headers = auth_headers(client, staff)

    def ids(q: str) -> list[int]:
        response = client.get("/fax-portal/referrals/search", params={"q": q}, headers=headers)

        assert response.status_code == 200

        return [item["id"] for item in response.json()]

    assert ids("delgado") == [maria.id]
    assert ids("0187") == [maria.id]
    assert ids("(214) 555-0187") == [maria.id]
    assert ids("zzz") == []
    assert maria.id in ids("")  # no text: most recent referrals

    item = client.get(
        "/fax-portal/referrals/search", params={"q": "maria"}, headers=headers
    ).json()[0]
    assert (item["display_name"], item["status"], item["status_label"]) == (
        "Maria Delgado",
        "ready_for_contact",
        "Ready for Contact",
    )


def test_module_exposes_the_call_limit() -> None:
    assert fax_portal_case.CALL_LIMIT == 5
