from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import add_clinic_and_exam, auth_headers, make_card, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.acuity_client import AcuityApiError
from app.models.fax_portal import CCFpActivityLog, CCFpReferralBoard, CCFpReferralExam
from app.services import fax_portal_booking, fax_portal_direct_booking

DAY = "2026-10-20"
MORNING = "2026-10-20T10:00:00-0500"
AFTERNOON = "2026-10-20T15:30:00-0500"

# An intake form like Acuity's: answers from the referral, three required
# consent checkboxes, an optional checkbox and a file upload.
FORM_FIELDS: list[dict[str, Any]] = [
    {
        "id": 101,
        "name": "Reason for the appointment:",
        "type": "dropdown",
        "required": True,
        "options": ["Abdominal pain", "Other"],
    },
    {"id": 102, "name": "If 'Other' please explain below:", "type": "textbox"},
    {
        "id": 103,
        "name": "Gender:",
        "type": "dropdown",
        "required": True,
        "options": ["Male", "Female"],
    },
    {"id": 104, "name": "Date of Birth (MM/DD/YYYY):", "type": "textbox", "required": True},
    {"id": 105, "name": "Family / Referring Physician:", "type": "textbox"},
    {"id": 106, "name": "(Optional) Please upload Doctor Order Below", "type": "file"},
    {
        "id": 107,
        "name": "Consent to receive texts from ScanX",
        "type": "checkbox",
        "required": True,
    },
    {
        "id": 108,
        "name": "I acknowledge the Notice of Privacy Practices",
        "type": "checkbox",
        "required": True,
    },
    {"id": 109, "name": "I accept the Terms of Service", "type": "checkbox", "required": True},
    {"id": 110, "name": "Release of ultrasound images", "type": "checkbox", "required": False},
]


class FakeAcuity:
    """Stands in for Acuity's availability and booking calls."""

    def __init__(self) -> None:
        self.times = {DAY: [MORNING, AFTERNOON]}
        self.booked: list[tuple[dict[str, Any], bool]] = []
        self.error: Exception | None = None

    def dates(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"date": "2026-10-22"}, {"date": DAY}]

    def slots(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"time": t, "slotsAvailable": 1} for t in self.times.get(kwargs["date"], [])]

    def create(self, body: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
        self.booked.append((body, admin))

        if self.error is not None:
            raise self.error

        return {"id": 98765, "datetime": body["datetime"]}


@pytest.fixture
def acuity(monkeypatch: pytest.MonkeyPatch) -> FakeAcuity:
    fake = FakeAcuity()
    monkeypatch.setattr(fax_portal_direct_booking, "list_availability_dates", fake.dates)
    monkeypatch.setattr(fax_portal_direct_booking, "list_availability_times", fake.slots)
    monkeypatch.setattr(fax_portal_direct_booking, "create_appointment", fake.create)

    return fake


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "front_desk")


def bookable(db: Session, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> CCFpReferralBoard:
    """A referral in Ready for Contact with a clinic, Exam 1 and the Acuity form."""
    defaults: dict[str, Any] = {
        "status": "ready_for_contact",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+12145550187",
        "phone_e164": "+12145550187",
        "email": "john@example.com",
        "gender": "Male",
        "dob": date(1977, 11, 10),
        "reason": "Abdominal pain",
        "physician_name": "Dr. Kim",
    }
    card = make_card(db, **{**defaults, **kwargs})
    add_clinic_and_exam(db, card)
    db.commit()

    exam_id = db.scalar(
        select(CCFpReferralExam.acuity_appointment_type_id).where(
            CCFpReferralExam.referral_id == card.id
        )
    )
    monkeypatch.setattr(
        fax_portal_booking,
        "list_forms",
        lambda: [{"id": 1, "appointmentTypeIDs": [exam_id], "fields": FORM_FIELDS}],
    )
    monkeypatch.setattr(fax_portal_booking, "_forms_cache", None)

    return card


def book(client: TestClient, headers: dict[str, str], card_id: int, **body: Any):
    return client.post(
        f"/fax-portal/referrals/{card_id}/direct-booking",
        json={"time": MORNING, **body},
        headers=headers,
    )


# ---------------------------------------------------------------------
# Open dates and times
# ---------------------------------------------------------------------


def test_open_dates_come_from_acuity_in_order(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)

    response = client.get(
        f"/fax-portal/referrals/{card.id}/direct-booking/dates?month=2026-10",
        headers=auth_headers(client, staff),
    )

    assert response.status_code == 200 and response.json() == [DAY, "2026-10-22"]


def test_open_times_are_labelled_in_the_clinics_timezone(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)

    response = client.get(
        f"/fax-portal/referrals/{card.id}/direct-booking/times?date={DAY}",
        headers=auth_headers(client, staff),
    )

    assert response.json() == [
        {"time": MORNING, "label": "10:00 AM"},
        {"time": AFTERNOON, "label": "3:30 PM"},
    ]


@pytest.mark.parametrize("query", ["month=October", "month=2026-13", "date=tomorrow"])
def test_malformed_dates_are_rejected(
    client: TestClient,
    db_session: Session,
    staff: Any,
    acuity: FakeAcuity,
    monkeypatch: Any,
    query: str,
) -> None:
    card = bookable(db_session, monkeypatch)
    kind = "dates" if query.startswith("month") else "times"

    response = client.get(
        f"/fax-portal/referrals/{card.id}/direct-booking/{kind}?{query}",
        headers=auth_headers(client, staff),
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------


def test_booking_creates_the_appointment_and_marks_the_referral_booked(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 200, response.text
    assert response.json()["appointment_id"] == "98765"
    assert response.json()["status"] == "appointment_booked"
    assert response.json()["when_label"] == "Oct 20 at 10:00 AM"

    [(body, admin)] = acuity.booked
    assert admin is False
    assert (body["datetime"], body["firstName"], body["lastName"]) == (MORNING, "John", "Doe")
    assert body["email"] == "john@example.com" and body["phone"] == "(214) 555-0187"
    assert body["calendarID"] == card.acuity_calendar_id

    db_session.refresh(card)
    assert card.status == "appointment_booked"
    assert (card.acuity_appointment_id, card.booked_source) == ("98765", "agent")
    assert card.booked_by_user_id == staff.id


def test_the_intake_answers_and_required_consents_are_sent(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)

    book(client, auth_headers(client, staff), card.id)

    [(body, _)] = acuity.booked
    answers = {a["id"]: a["value"] for a in body["fields"]}
    # From the referral:
    assert answers[101] == "Abdominal pain" and answers[103] == "Male"
    assert answers[104] == "11/10/1977" and answers[105] == "Dr. Kim"
    # The three required consent checkboxes are sent as yes ...
    assert (answers[107], answers[108], answers[109]) == ("yes", "yes", "yes")
    # ... and nothing is sent for the optional checkbox or the file upload.
    assert 110 not in answers and 106 not in answers and 102 not in answers


def test_the_audit_entry_says_the_consents_were_sent_automatically(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)

    book(client, auth_headers(client, staff), card.id)

    entry = db_session.scalar(
        select(CCFpActivityLog).where(CCFpActivityLog.action == "direct_book")
    )
    assert entry.user_id == staff.id and entry.referral_id == card.id
    assert entry.details == {
        "appointment_id": "98765",
        "paid": False,
        "consents": "sent_as_yes_automatically",
        "admin_mode": False,
    }
    # No names, numbers or answers in the audit details.
    assert "John" not in str(entry.details) and "555" not in str(entry.details)


def test_without_an_email_the_booking_is_made_as_admin(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch, email=None)

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 200, response.text
    [(body, admin)] = acuity.booked
    assert admin is True and "email" not in body


def test_an_email_typed_in_the_dialog_is_used_and_saved(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch, email=None)

    book(client, auth_headers(client, staff), card.id, email="  new@example.com ")

    [(body, admin)] = acuity.booked
    assert body["email"] == "new@example.com" and admin is False
    db_session.refresh(card)
    assert card.email == "new@example.com"


def test_only_a_time_acuity_currently_offers_can_be_booked(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)

    response = book(client, auth_headers(client, staff), card.id, time="2026-10-20T03:00:00-0500")

    assert response.status_code == 409 and "no longer available" in response.json()["detail"]
    assert acuity.booked == []


def test_a_time_taken_in_the_meantime_is_a_conflict_and_nothing_changes(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)
    acuity.error = AcuityApiError("not available", code="not_available")

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 409 and "just taken" in response.json()["detail"]
    db_session.refresh(card)
    assert card.status == "ready_for_contact" and card.acuity_appointment_id is None


def test_a_required_question_the_referral_cannot_answer_blocks_the_booking(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch, reason=None, dob=None)

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 422
    assert "Reason for the appointment" in response.json()["detail"]
    assert "Date of Birth" in response.json()["detail"]
    assert acuity.booked == []


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "review_required"},
        {"status": "appointment_booked"},
        {"first_name": None},
    ],
)
def test_referrals_that_cannot_be_booked_are_refused(
    client: TestClient,
    db_session: Session,
    staff: Any,
    acuity: FakeAcuity,
    monkeypatch: Any,
    changes: dict[str, Any],
) -> None:
    card = bookable(db_session, monkeypatch, **changes)

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 422 and acuity.booked == []


def test_a_clinic_and_exam_are_needed_first(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity
) -> None:
    card = make_card(db_session, status="ready_for_contact", first_name="Ann", last_name="Lee")
    db_session.commit()

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 422 and "Exam 1 and the clinic" in response.json()["detail"]


def test_if_acuity_booked_but_the_referral_cannot_be_updated_the_agent_is_told_the_id(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    other = bookable(db_session, monkeypatch)
    other.acuity_appointment_id = "98765"  # that appointment already belongs to another referral
    db_session.commit()
    card = bookable(db_session, monkeypatch)

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 422 and "98765" in response.json()["detail"]
    db_session.refresh(card)
    assert card.status == "ready_for_contact"


def test_an_unreachable_acuity_is_a_bad_gateway_with_a_plain_message(
    client: TestClient, db_session: Session, staff: Any, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)
    acuity.error = AcuityApiError("Could not reach Acuity: timeout")

    response = book(client, auth_headers(client, staff), card.id)

    assert response.status_code == 502 and "timeout" not in response.text


def test_direct_booking_needs_portal_access(
    client: TestClient, db_session: Session, acuity: FakeAcuity, monkeypatch: Any
) -> None:
    card = bookable(db_session, monkeypatch)
    sonographer = make_user(db_session, "sonographer")

    response = book(client, auth_headers(client, sonographer), card.id)

    assert response.status_code == 403 and acuity.booked == []
