from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fax_portal_helpers import add_clinic_and_exam, make_card
from sqlalchemy.orm import Session

from app.integrations.acuity_client import AcuityApiError
from app.models.fax_portal import CCFpAcuityAppointmentType, CCFpReferralBoard, CCFpReferralExam
from app.services import fax_portal_booking

# The shape of Acuity's GET /forms for the "Abdominal" intake form.
ABDOMINAL_FIELDS: list[dict[str, Any]] = [
    {
        "id": 101,
        "name": "Reason for the appointment:",
        "type": "dropdown",
        "options": ["Abdominal pain", "Bloating", "Other"],
    },
    {"id": 102, "name": "If 'Other' please explain below:", "type": "textbox"},
    {"id": 103, "name": "Gender:", "type": "dropdown", "options": ["Male", "Female"]},
    {"id": 104, "name": "Date of Birth (MM/DD/YYYY):", "type": "textbox"},
    {"id": 105, "name": "Family / Referring Physician:", "type": "textbox"},
    {"id": 106, "name": "Physician Fax # (E.g. 4694207432):", "type": "textbox"},
    {"id": 107, "name": "Physician Email Address", "type": "textbox"},
    {"id": 108, "name": "(Optional) Please upload Doctor Order Below", "type": "file"},
    {"id": 109, "name": "Consent to receive appointment texts from ScanX", "type": "checkbox"},
]


def referral(**kwargs: Any) -> CCFpReferralBoard:
    defaults: dict[str, Any] = {
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+15550101234",
        "phone_e164": "+15550101234",
        "gender": "female",
        "dob": date(1977, 11, 10),
        "reason": "abdominal pain",
        "physician_name": "Sunjoo Oh",
        "physician_fax_no": "(972) 943-8348",
        "physician_email": "oh@example.com",
    }

    return CCFpReferralBoard(**{**defaults, **kwargs})


def values(card: CCFpReferralBoard, fields: list[dict[str, Any]] = ABDOMINAL_FIELDS) -> dict:
    return dict(fax_portal_booking.intake_field_values(card, fields))


def test_the_intake_questions_are_filled_from_the_referral() -> None:
    assert values(referral()) == {
        "field:101": "Abdominal pain",  # Acuity's own spelling of the option
        "field:103": "Female",
        "field:104": "11/10/1977",
        "field:105": "Sunjoo Oh",
        "field:106": "9729438348",
        "field:107": "oh@example.com",
    }


def test_consents_and_the_file_upload_are_never_filled() -> None:
    assert "field:108" not in values(referral()) and "field:109" not in values(referral())


@pytest.mark.parametrize(("typed", "expected"), [("F", "Female"), ("m", "Male"), ("MALE", "Male")])
def test_gender_is_matched_to_the_options_acuity_lists(typed: str, expected: str) -> None:
    assert values(referral(gender=typed))["field:103"] == expected


def test_a_gender_acuity_does_not_list_is_left_for_the_patient() -> None:
    assert "field:103" not in values(referral(gender="Unknown"))


def test_a_reason_acuity_does_not_list_goes_in_as_other_with_the_explanation() -> None:
    result = values(referral(reason="Dull ache after meals"))

    assert result["field:101"] == "Other"
    assert result["field:102"] == "Dull ache after meals"


def test_the_explanation_box_is_only_used_when_the_reason_is_other() -> None:
    assert "field:102" not in values(referral(reason="Bloating", reason_other="ignored"))
    assert values(referral(reason="Other", reason_other="Travel"))["field:102"] == "Travel"


def test_what_the_referral_does_not_have_is_left_blank() -> None:
    result = values(
        referral(
            reason=None,
            gender=None,
            dob=None,
            physician_name=None,
            physician_fax_no="555",
            physician_email=None,
        )
    )

    assert result == {}


def test_forms_without_those_questions_add_nothing() -> None:
    assert values(referral(), [{"id": 1, "name": "Something else", "type": "textbox"}]) == {}


# ---------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------


def card_with_exam(db: Session) -> tuple[CCFpReferralBoard, int]:
    card = make_card(
        db,
        first_name="John",
        last_name="Doe",
        phone="+15550101234",
        phone_e164="+15550101234",
        gender="Female",
        dob=date(1977, 11, 10),
        reason="Abdominal pain",
        status="ready_for_contact",
    )
    add_clinic_and_exam(db, card)
    exam = db.query(CCFpReferralExam).filter_by(referral_id=card.id, slot=1).one()
    db.query(CCFpAcuityAppointmentType).filter_by(
        acuity_appointment_type_id=exam.acuity_appointment_type_id
    ).update({"scheduling_url": "https://app.acuityscheduling.com/schedule.php?owner=1"})
    db.commit()

    return card, exam.acuity_appointment_type_id


@pytest.fixture
def acuity_forms(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fakes GET /forms (the form is attached to whichever exam is set in
    state["exam"]) and counts the calls."""
    state: dict[str, Any] = {"exam": 0, "calls": 0}

    def fake_forms() -> list[dict[str, Any]]:
        state["calls"] += 1

        return [{"id": 1, "appointmentTypeIDs": [state["exam"]], "fields": ABDOMINAL_FIELDS}]

    monkeypatch.setattr(fax_portal_booking, "list_forms", fake_forms)
    monkeypatch.setattr(fax_portal_booking, "_forms_cache", None)

    return state


def query(link: str | None) -> dict[str, list[str]]:
    assert link is not None

    return parse_qs(urlsplit(link).query)


def test_the_agents_link_carries_the_intake_answers(
    db_session: Session, acuity_forms: dict[str, Any]
) -> None:
    card, exam_id = card_with_exam(db_session)
    acuity_forms["exam"] = exam_id

    link, blocked = fax_portal_booking.build_booking_link(
        db_session, card, include_intake_fields=True
    )

    assert blocked is None
    q = query(link)
    assert q["field:101"] == ["Abdominal pain"] and q["field:104"] == ["11/10/1977"]
    # Acuity's phone box reads the national form.
    assert q["phone"] == ["(555) 010-1234"] and q["firstName"] == ["John"]


def test_the_link_texted_to_the_patient_carries_no_intake_answers(
    db_session: Session, acuity_forms: dict[str, Any]
) -> None:
    card, exam_id = card_with_exam(db_session)
    acuity_forms["exam"] = exam_id

    link, _ = fax_portal_booking.build_booking_link(db_session, card)

    assert not any(key.startswith("field:") for key in query(link))
    assert "11%2F10%2F1977" not in (link or "") and acuity_forms["calls"] == 0


def test_acuitys_forms_are_fetched_once_and_cached(
    db_session: Session, acuity_forms: dict[str, Any]
) -> None:
    card, exam_id = card_with_exam(db_session)
    acuity_forms["exam"] = exam_id

    for _ in range(3):
        fax_portal_booking.build_booking_link(db_session, card, include_intake_fields=True)

    assert acuity_forms["calls"] == 1


def test_when_acuity_cannot_be_reached_the_link_is_still_built(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down() -> list[dict[str, Any]]:
        raise AcuityApiError("Could not reach Acuity")

    monkeypatch.setattr(fax_portal_booking, "list_forms", down)
    monkeypatch.setattr(fax_portal_booking, "_forms_cache", None)
    card, _ = card_with_exam(db_session)

    link, blocked = fax_portal_booking.build_booking_link(
        db_session, card, include_intake_fields=True
    )

    assert blocked is None
    q = query(link)
    assert q["firstName"] == ["John"] and not any(key.startswith("field:") for key in q)
