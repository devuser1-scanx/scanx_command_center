from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_card, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations import acuity_client
from app.models.fax_portal import CCFpActivityLog, CCFpAcuityAppointmentType, CCFpReferralBoard
from app.services.fax_portal_acuity import (
    exams_without_reasons,
    reason_info_by_exam,
    run_acuity_sync,
)

CALENDARS: list[dict[str, Any]] = [{"id": 1, "name": "Dallas Location"}]


def exam(type_id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {"id": type_id, "name": name, "active": True, "calendarIDs": [1], **extra}


# Recorded in the shape of Acuity's GET /appointment-types and GET /forms.
TYPES: list[dict[str, Any]] = [
    exam(1, "Aorta / AAA Screening", formIDs=[100]),
    # Two exams that share one form (matched through the form's appointmentTypeIDs,
    # and through the exam's own formIDs).
    exam(2, "Baseline AFC", formIDs=[200]),
    exam(3, "Follicle Monitoring"),
    exam(4, "Exam With No Form"),
    exam(5, "Form Without A Reason Field", formIDs=[300]),
]

FORMS: list[dict[str, Any]] = [
    {
        "id": 100,
        "name": "AAA / Aorta Screening",
        "appointmentTypeIDs": [1],
        "fields": [
            {
                "id": 11,
                "name": "  Reason for the appointment:  ",
                "type": "dropdown",
                "required": True,
                # Stray whitespace in what Acuity returns.
                "options": [
                    " Routine vascular screening ",
                    "Family history of aneurysm",
                    "Other",
                    " ",
                ],
            },
            {"id": 12, "name": "If 'Other' please explain below:", "type": "textbox"},
        ],
    },
    {
        "id": 200,
        "name": "Fertility",
        "appointmentTypeIDs": [2, 3],
        "fields": [
            {
                "id": 21,
                "name": "reason for the appointment",
                "type": "dropdown",
                "options": ["Antral follicle count", "Other"],
            }
        ],
    },
    {
        "id": 300,
        "name": "Plain form",
        "appointmentTypeIDs": [5],
        "fields": [
            {"id": 31, "name": "Gender:", "type": "dropdown", "options": ["Male", "Female"]}
        ],
    },
]


def sync(db: Session, types: Any = None, forms: Any = None):
    return run_acuity_sync(
        db,
        trigger="scheduled",
        fetch_calendars=lambda: copy.deepcopy(CALENDARS),
        fetch_types=lambda: copy.deepcopy(TYPES if types is None else types),
        fetch_forms=lambda: copy.deepcopy(FORMS if forms is None else forms),
    )


def row(db: Session, type_id: int) -> CCFpAcuityAppointmentType:
    db.expire_all()

    return db.get(CCFpAcuityAppointmentType, type_id)


# ---------------------------------------------------------------------
# Matching an exam to its form's reason options
# ---------------------------------------------------------------------


def test_each_exam_gets_the_reasons_from_its_form() -> None:
    info = reason_info_by_exam(TYPES, FORMS)

    # Trimmed, in Acuity's order, blanks dropped; the field matched case-insensitively.
    assert info[1] == (
        100,
        11,
        ["Routine vascular screening", "Family history of aneurysm", "Other"],
    )
    assert info[2] == (200, 21, ["Antral follicle count", "Other"])


def test_exams_sharing_a_form_get_the_same_options() -> None:
    info = reason_info_by_exam(TYPES, FORMS)

    # Exam 3 has no formIDs of its own: the form lists it.
    assert info[3] == info[2]


def test_an_exam_without_a_form_or_a_reason_field_has_no_options() -> None:
    info = reason_info_by_exam(TYPES, FORMS)

    assert info[4] == (None, None, [])
    assert info[5] == (None, None, [])


def test_the_exams_own_form_list_wins_and_the_first_form_with_a_reason_field_is_used() -> None:
    types = [exam(1, "Exam", formIDs=[300, 100])]  # 300 has no reason field

    assert reason_info_by_exam(types, FORMS)[1][0] == 100


# ---------------------------------------------------------------------
# The sync
# ---------------------------------------------------------------------


def test_the_sync_stores_the_form_the_field_and_the_options(db_session: Session) -> None:
    run = sync(db_session)

    assert run.status == "success"
    aorta = row(db_session, 1)
    assert (aorta.acuity_form_id, aorta.reason_field_id) == (100, 11)
    assert aorta.reason_options == [
        "Routine vascular screening",
        "Family history of aneurysm",
        "Other",
    ]
    assert row(db_session, 4).reason_options == []
    assert (row(db_session, 4).acuity_form_id, row(db_session, 4).reason_field_id) == (None, None)


def test_the_sync_reports_the_exams_without_reason_options(db_session: Session) -> None:
    sync(db_session)

    assert exams_without_reasons(db_session) == ["Exam With No Form", "Form Without A Reason Field"]


def test_a_second_run_changes_nothing(db_session: Session) -> None:
    sync(db_session)

    again = sync(db_session)

    assert (again.types_added, again.types_updated, again.types_removed) == (0, 0, 0)
    assert again.changes is None


def test_a_changed_option_list_is_an_update_and_is_recorded(db_session: Session) -> None:
    sync(db_session)
    forms = copy.deepcopy(FORMS)
    forms[0]["fields"][0]["options"] = ["Routine vascular screening", "Smoking history", "Other"]

    run = sync(db_session, forms=forms)

    assert run.types_updated == 1
    assert row(db_session, 1).reason_options == [
        "Routine vascular screening",
        "Smoking history",
        "Other",
    ]
    [change] = [c for c in run.changes if c["field"] == "reason_options"]
    assert change["kind"] == "type" and change["id"] == 1
    assert "Family history of aneurysm" in change["from"] and "Smoking history" in change["to"]


def test_removing_the_reason_field_clears_the_options(db_session: Session) -> None:
    sync(db_session)
    forms = copy.deepcopy(FORMS)
    forms[0]["fields"] = [forms[0]["fields"][1]]

    sync(db_session, forms=forms)

    assert row(db_session, 1).reason_options == []
    assert row(db_session, 1).reason_field_id is None


def test_no_forms_at_all_is_refused_so_the_options_are_not_wiped(db_session: Session) -> None:
    sync(db_session)

    run = sync(db_session, forms=[])

    assert run.status == "failed" and "no forms" in run.error_message
    assert row(db_session, 1).reason_options != []


def test_a_forms_outage_fails_the_run_and_changes_nothing(db_session: Session) -> None:
    sync(db_session)

    def down() -> list[dict[str, Any]]:
        raise acuity_client.AcuityApiError("Could not reach Acuity")

    run = run_acuity_sync(
        db_session,
        trigger="scheduled",
        fetch_calendars=lambda: copy.deepcopy(CALENDARS),
        fetch_types=lambda: copy.deepcopy(TYPES),
        fetch_forms=down,
    )

    assert run.status == "failed"
    assert row(db_session, 1).reason_options != []


# ---------------------------------------------------------------------
# The exam list API
# ---------------------------------------------------------------------


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "sales")


def test_the_exam_list_includes_each_exams_reason_options(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    sync(db_session)

    response = client.get(
        "/fax-portal/acuity/appointment-types", headers=auth_headers(client, staff)
    )

    assert response.status_code == 200, response.text
    by_name = {t["name"]: t["reason_options"] for t in response.json()}
    assert by_name["Aorta / AAA Screening"][-1] == "Other"
    assert by_name["Baseline AFC"] == ["Antral follicle count", "Other"]
    assert by_name["Exam With No Form"] == []


# ---------------------------------------------------------------------
# Saving a reason in Intake Review
# ---------------------------------------------------------------------


def review_card(db: Session, **kwargs: Any) -> CCFpReferralBoard:
    card = make_card(db, status="review_required", first_name="Ann", last_name="Lee", **kwargs)
    db.commit()

    return card


def save(client: TestClient, db: Session, staff: Any, card: CCFpReferralBoard, **body: Any):
    db.refresh(card)

    return client.patch(
        f"/fax-portal/referrals/{card.id}/intake",
        json={"row_version": card.row_version, **body},
        headers=auth_headers(client, staff),
    )


@pytest.fixture
def aorta_card(db_session: Session, client: TestClient, staff: Any) -> CCFpReferralBoard:
    """A card in Intake Review with Aorta / AAA Screening as Exam 1."""
    sync(db_session)
    card = review_card(db_session)

    assert save(client, db_session, staff, card, exam_1_id=1).status_code == 200

    return card


def test_a_reason_must_be_one_of_exam_1s_options(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    refused = save(client, db_session, staff, aorta_card, reason="Because I said so")

    assert refused.status_code == 422 and "reasons offered" in refused.json()["detail"]

    other_exams_option = save(client, db_session, staff, aorta_card, reason="Antral follicle count")
    assert other_exams_option.status_code == 422  # another exam's option

    ok = save(client, db_session, staff, aorta_card, reason="Family history of aneurysm")
    assert ok.status_code == 200, ok.text
    db_session.refresh(aorta_card)
    assert aorta_card.reason == "Family history of aneurysm"


def test_the_reason_is_saved_in_acuitys_spelling(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    save(client, db_session, staff, aorta_card, reason="  routine VASCULAR screening ")

    db_session.refresh(aorta_card)
    assert aorta_card.reason == "Routine vascular screening"


def test_other_needs_the_explanation(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    without = save(client, db_session, staff, aorta_card, reason="Other")
    blank = save(client, db_session, staff, aorta_card, reason="Other", reason_other="   ")
    with_text = save(client, db_session, staff, aorta_card, reason="Other", reason_other="Travel")

    assert without.status_code == 422 and "Other reason" in without.json()["detail"]
    assert blank.status_code == 422
    assert with_text.status_code == 200, with_text.text
    db_session.refresh(aorta_card)
    assert (aorta_card.reason, aorta_card.reason_other) == ("Other", "Travel")


def test_the_other_text_box_is_kept_whatever_the_reason_is(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    response = save(
        client,
        db_session,
        staff,
        aorta_card,
        reason="Family history of aneurysm",
        reason_other="Also mentioned chest pain",
    )

    assert response.status_code == 200, response.text
    db_session.refresh(aorta_card)
    assert aorta_card.reason_other == "Also mentioned chest pain"


def test_a_reason_cannot_be_chosen_before_exam_1_or_when_it_has_no_options(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    sync(db_session)
    card = review_card(db_session)

    no_exam = save(client, db_session, staff, card, reason="Other", reason_other="x")
    assert no_exam.status_code == 422 and "Choose Exam 1" in no_exam.json()["detail"]

    assert save(client, db_session, staff, card, exam_1_id=4).status_code == 200
    none_offered = save(client, db_session, staff, card, reason="Other", reason_other="x")
    assert none_offered.status_code == 422 and "no reason options" in none_offered.json()["detail"]


def test_changing_exam_1_clears_a_reason_the_new_exam_does_not_offer(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    save(client, db_session, staff, aorta_card, reason="Family history of aneurysm")

    response = save(client, db_session, staff, aorta_card, exam_1_id=2)

    assert response.status_code == 200, response.text
    assert "reason" in response.json()["changed_fields"]
    db_session.refresh(aorta_card)
    assert aorta_card.reason is None


def test_changing_exam_1_keeps_a_reason_the_new_exam_also_offers(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    save(client, db_session, staff, aorta_card, reason="Other", reason_other="Travel")

    # Baseline AFC also lists "Other".
    response = save(client, db_session, staff, aorta_card, exam_1_id=2)

    assert response.status_code == 200, response.text
    db_session.refresh(aorta_card)
    assert (aorta_card.reason, aorta_card.reason_other) == ("Other", "Travel")


def test_reason_edits_are_audited_with_before_and_after(
    client: TestClient, db_session: Session, staff: Any, aorta_card: CCFpReferralBoard
) -> None:
    save(client, db_session, staff, aorta_card, reason="Family history of aneurysm")
    save(client, db_session, staff, aorta_card, reason="Routine vascular screening")

    entries = db_session.scalars(
        select(CCFpActivityLog)
        .where(CCFpActivityLog.referral_id == aorta_card.id, CCFpActivityLog.action == "edit_field")
        .order_by(CCFpActivityLog.id)
    ).all()
    assert entries[-1].changes["reason"] == {
        "from": "Family history of aneurysm",
        "to": "Routine vascular screening",
    }


def test_edits_that_do_not_touch_the_reason_are_not_checked_against_it(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    # An older card whose reason was typed freely: other edits still work.
    sync(db_session)
    card = review_card(db_session, reason="Abdominal pain (typed)")

    response = save(client, db_session, staff, card, first_name="Anna")

    assert response.status_code == 200, response.text
    db_session.refresh(card)
    assert card.reason == "Abdominal pain (typed)"
