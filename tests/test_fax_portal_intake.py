from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_card, make_fax, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations import gcs_faxes
from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpAcuityAppointmentType,
    CCFpAcuityCalendar,
    CCFpFax,
    CCFpFaxDocument,
    CCFpFaxPatient,
    CCFpReferralExam,
)

PDF = b"%PDF-1.4\n% test pdf\n%%EOF"


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "front_desk")


@pytest.fixture
def acuity(db_session: Session) -> dict[str, int]:
    """A synced clinic and exam list, as the Acuity sync would leave it."""
    for calendar_id, name in ((1, "Dallas"), (2, "Fairview")):
        db_session.add(CCFpAcuityCalendar(acuity_calendar_id=calendar_id, name=name, raw={}))

    for type_id, name in (
        (10, "Carotid Doppler Ultrasound"),
        (11, "Abdominal Ultrasound (Limited)"),
        (12, "Scrotal Ultrasound"),
    ):
        db_session.add(
            CCFpAcuityAppointmentType(acuity_appointment_type_id=type_id, name=name, raw={})
        )

    db_session.add(
        CCFpAcuityAppointmentType(
            acuity_appointment_type_id=13, name="Retired Exam", is_active=False, raw={}
        )
    )
    db_session.commit()

    return {"dallas": 1, "fairview": 2, "carotid": 10, "abd": 11, "scrotal": 12, "retired": 13}


def intake_card(db: Session, fax: CCFpFax | None = None, **kwargs: Any):
    kwargs.setdefault("status", "review_required")
    card = make_card(db, fax, **kwargs)
    db.commit()

    return card


def detail(client: TestClient, headers: dict[str, str], card_id: int) -> dict[str, Any]:
    response = client.get(f"/fax-portal/referrals/{card_id}/intake", headers=headers)

    assert response.status_code == 200, response.text

    return response.json()


# ---------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------


def test_queue_lists_review_cards_newest_first_whether_or_not_opened(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    opened_recent = intake_card(
        db_session, first_name="Opened", created_at=ago(minutes=1), first_viewed_at=ago(seconds=5)
    )
    new_old = intake_card(db_session, first_name="NewOld", created_at=ago(hours=3))
    new_recent = intake_card(db_session, first_name="NewRecent", created_at=ago(minutes=5))
    intake_card(db_session, first_name="Released", status="ready_for_contact")

    response = client.get("/fax-portal/intake/queue", headers=auth_headers(client, staff))
    body = response.json()

    # Opening a fax does not move it: the order is arrival time only, so a
    # fax does not jump to another page while it is being reviewed.
    assert [i["id"] for i in body["items"]] == [opened_recent.id, new_recent.id, new_old.id]
    assert body["waiting_count"] == 3
    assert [i["is_new"] for i in body["items"]] == [False, True, True]


def test_queue_duplicates_filter_and_counts(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    first = intake_card(db_session, first_name="Ann")
    dup = intake_card(db_session, first_name="Ann", duplicate_of_referral_id=first.id)

    headers = auth_headers(client, staff)
    everyone = client.get("/fax-portal/intake/queue", headers=headers).json()
    only_dups = client.get(
        "/fax-portal/intake/queue", params={"duplicates_only": True}, headers=headers
    ).json()

    assert (everyone["waiting_count"], everyone["duplicate_count"]) == (2, 1)
    assert [i["id"] for i in only_dups["items"]] == [dup.id]
    assert only_dups["items"][0]["possible_duplicate"] is True
    # The tab counts stay the whole queue's, whichever tab is open.
    assert (only_dups["waiting_count"], only_dups["duplicate_count"]) == (2, 1)


def test_queue_is_paged_ten_at_a_time(client: TestClient, db_session: Session, staff: Any) -> None:
    cards = [
        intake_card(db_session, first_name=f"P{i:02d}", created_at=ago(minutes=i))
        for i in range(1, 26)
    ]
    headers = auth_headers(client, staff)

    def page(n: int, **params: Any) -> dict[str, Any]:
        response = client.get(
            "/fax-portal/intake/queue", params={"page": n, **params}, headers=headers
        )
        assert response.status_code == 200

        return response.json()

    first, second, third = page(1), page(2), page(3)

    assert [len(p["items"]) for p in (first, second, third)] == [10, 10, 5]
    assert (first["total"], first["page"], first["page_size"], first["total_pages"]) == (
        25,
        1,
        10,
        3,
    )
    # Newest first, continuing across pages with nothing repeated or skipped.
    ids = [i["id"] for p in (first, second, third) for i in p["items"]]
    assert ids == [c.id for c in cards]
    # Tab counts describe the whole queue, not the page.
    assert (third["waiting_count"], third["duplicate_count"]) == (25, 0)
    # Past the end is an empty page, not an error.
    beyond = page(4)
    assert beyond["items"] == [] and beyond["total"] == 25
    # A custom size is honoured.
    assert len(page(1, page_size=20)["items"]) == 20


def test_queue_page_limits(client: TestClient, db_session: Session, staff: Any) -> None:
    headers = auth_headers(client, staff)

    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 51}):
        response = client.get("/fax-portal/intake/queue", params=params, headers=headers)

        assert response.status_code == 422, params


def test_duplicates_tab_is_paged_on_its_own_total(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    original = intake_card(db_session, first_name="Orig")
    for i in range(12):
        intake_card(db_session, first_name=f"Dup{i}", duplicate_of_referral_id=original.id)

    body = client.get(
        "/fax-portal/intake/queue",
        params={"duplicates_only": True, "page": 2},
        headers=auth_headers(client, staff),
    ).json()

    assert (body["total"], body["total_pages"], len(body["items"])) == (12, 2, 2)
    assert (body["waiting_count"], body["duplicate_count"]) == (13, 12)


def test_intake_endpoints_need_access(client: TestClient, db_session: Session, staff: Any) -> None:
    card = intake_card(db_session)
    sono = auth_headers(client, make_user(db_session, "sonographer"))

    for method, path in (
        ("get", "/fax-portal/intake/queue"),
        ("get", f"/fax-portal/referrals/{card.id}/intake"),
        ("post", f"/fax-portal/referrals/{card.id}/viewed"),
        ("patch", f"/fax-portal/referrals/{card.id}/intake"),
        ("get", f"/fax-portal/faxes/{card.fax_id}/pdf"),
    ):
        assert getattr(client, method)(path).status_code == 401, path
        assert getattr(client, method)(path, headers=sono).status_code == 403, path


# ---------------------------------------------------------------------
# Detail and prefill
# ---------------------------------------------------------------------


def test_detail_has_fields_fax_and_documents(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    fax = make_fax(db_session)
    fax.summary = "A referral from Dr Kim for a carotid Doppler."
    fax.page_count = 5
    db_session.add(
        CCFpFaxDocument(
            fax_id=fax.id, idx=0, name="Cover", doc_type="Cover Page", page_start=1, page_end=1
        )
    )
    db_session.add(
        CCFpFaxDocument(
            fax_id=fax.id, idx=1, name="Referral", doc_type="Referral", page_start=2, page_end=3
        )
    )
    card = intake_card(
        db_session,
        fax,
        first_name="Ann",
        last_name="Alpha",
        phone="(214) 555-0111",
        physician_name="Dr. Kim",
        is_urgent=True,
    )

    body = detail(client, auth_headers(client, staff), card.id)

    assert body["row_version"] == 1
    assert body["is_new"] is True
    assert body["is_urgent"] is True
    assert body["fields"]["first_name"] == "Ann"
    assert body["fields"]["phone"] == "(214) 555-0111"
    assert body["clinic"] is None and body["exam_1"] is None
    assert body["fax"]["summary"] == "A referral from Dr Kim for a carotid Doppler."
    assert body["fax"]["page_count"] == 5
    assert body["fax"]["has_pdf"] is False
    assert [(d["name"], d["page_start"], d["page_end"]) for d in body["fax"]["documents"]] == [
        ("Cover", 1, 1),
        ("Referral", 2, 3),
    ]


def test_detail_returns_each_documents_own_phi_summary(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    fax = make_fax(db_session)
    fax.summary = "Whole-fax summary: not what the form shows."
    patients = [
        CCFpFaxPatient(fax_id=fax.id, idx=0, full_name="Ann Alpha"),
        CCFpFaxPatient(fax_id=fax.id, idx=1, full_name="Ben Beta"),
    ]
    db_session.add_all(patients)
    db_session.flush()
    db_session.add_all(
        [
            CCFpFaxDocument(fax_id=fax.id, idx=0, name="Cover", summary="Fax cover sheet."),
            CCFpFaxDocument(
                fax_id=fax.id, idx=1, name="Ann referral", patient_idx=0, summary="Ann: carotid."
            ),
            CCFpFaxDocument(
                fax_id=fax.id, idx=2, name="Ben referral", patient_idx=1, summary="Ben: abdomen."
            ),
        ]
    )
    card = intake_card(db_session, fax, fax_patient_id=patients[0].id)

    documents = detail(client, auth_headers(client, staff), card.id)["fax"]["documents"]

    assert [(d["name"], d["summary"], d["for_patient"]) for d in documents] == [
        ("Cover", "Fax cover sheet.", True),
        ("Ann referral", "Ann: carotid.", True),
        # Another patient's document is flagged so the form leaves it out.
        ("Ben referral", "Ben: abdomen.", False),
    ]


def test_the_patient_address_is_returned_and_can_be_corrected(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(db_session, first_name="Ann", address="12 Elm St, Dallas, TX 75201")
    headers = auth_headers(client, staff)

    assert detail(client, headers, card.id)["fields"]["address"] == "12 Elm St, Dallas, TX 75201"

    response = client.patch(
        f"/fax-portal/referrals/{card.id}/intake",
        json={"row_version": 1, "address": "  14 Elm St, Dallas, TX 75201 "},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed_fields"] == ["address"]
    db_session.refresh(card)
    assert card.address == "14 Elm St, Dallas, TX 75201"


def test_detail_suggests_exams_from_the_fax_text(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    fax = make_fax(db_session)
    fax.summary = "Orders a carotid Doppler ultrasound and a limited abdominal ultrasound."
    card = intake_card(db_session, fax)

    suggestions = detail(client, auth_headers(client, staff), card.id)["suggestions"]

    assert suggestions["exam_1"]["acuity_appointment_type_id"] == acuity["carotid"]
    assert suggestions["exam_2"]["acuity_appointment_type_id"] == acuity["abd"]
    assert "carotid" in suggestions["exam_1"]["fax_text"].lower()
    assert suggestions["clinic"] is None
    # Suggestions are never saved by looking at a card.
    assert db_session.scalars(select(CCFpReferralExam)).all() == []


def test_suggestions_skip_what_is_already_chosen_and_retired_types(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    fax = make_fax(db_session)
    fax.summary = "Carotid Doppler ultrasound, scrotal ultrasound and a retired exam."
    card = intake_card(db_session, fax)
    db_session.add(
        CCFpReferralExam(
            referral_id=card.id,
            slot=1,
            acuity_appointment_type_id=acuity["carotid"],
            appointment_type="Carotid Doppler Ultrasound",
        )
    )
    db_session.commit()

    body = detail(client, auth_headers(client, staff), card.id)

    assert body["exam_1"]["name"] == "Carotid Doppler Ultrasound"
    assert body["suggestions"]["exam_1"] is None  # slot 1 is already set
    assert body["suggestions"]["exam_2"]["acuity_appointment_type_id"] == acuity["scrotal"]


def test_detail_suggests_a_clinic_only_when_the_fax_names_one(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    fax = make_fax(db_session)
    fax.summary = "Please schedule at the Fairview Location, thank you."
    card = intake_card(db_session, fax)

    clinic = detail(client, auth_headers(client, staff), card.id)["suggestions"]["clinic"]

    assert clinic["acuity_calendar_id"] == acuity["fairview"]
    assert clinic["reason"] == "Named on the fax"


def test_detail_unknown_referral_is_404(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    response = client.get(
        "/fax-portal/referrals/999999/intake", headers=auth_headers(client, staff)
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------
# Opening a card clears NEW, audited
# ---------------------------------------------------------------------


def test_opening_a_card_clears_new_without_bumping_the_version(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)

    assert (
        client.post(f"/fax-portal/referrals/{card.id}/viewed", headers=headers).status_code == 204
    )

    db_session.refresh(card)
    assert card.first_viewed_at is not None
    assert card.first_viewed_by_user_id == staff.id
    # A look must not make someone else's drag on the board conflict.
    assert card.row_version == 1
    assert detail(client, headers, card.id)["is_new"] is False


def test_the_first_viewer_is_remembered_and_every_open_is_logged(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    other = make_user(db_session, "sales")
    card = intake_card(db_session)

    client.post(f"/fax-portal/referrals/{card.id}/viewed", headers=auth_headers(client, staff))
    client.post(f"/fax-portal/referrals/{card.id}/viewed", headers=auth_headers(client, other))

    db_session.refresh(card)
    assert card.first_viewed_by_user_id == staff.id

    opens = db_session.scalars(
        select(CCFpActivityLog).where(
            CCFpActivityLog.referral_id == card.id, CCFpActivityLog.action == "open_card"
        )
    ).all()
    assert sorted(o.user_id for o in opens) == sorted([staff.id, other.id])
    assert {o.section for o in opens} == {"intake_review"}


# ---------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------


def patch(client: TestClient, headers: dict[str, str], card_id: int, **body: Any):
    return client.patch(f"/fax-portal/referrals/{card_id}/intake", json=body, headers=headers)


def test_edits_go_to_the_card_with_an_audit_trail(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(db_session, first_name="Jon", last_name="Doe", phone="214-555-0000")
    headers = auth_headers(client, staff)

    response = patch(
        client,
        headers,
        card.id,
        row_version=1,
        first_name="  John ",
        dob="1970-01-31",
        phone="(214) 555-0187",
        physician_email="",
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": card.id,
        "row_version": 2,
        "changed_fields": ["dob", "first_name", "phone"],
    }

    db_session.refresh(card)
    assert card.first_name == "John"  # trimmed
    assert card.last_name == "Doe"  # not sent, not touched
    assert str(card.dob) == "1970-01-31"
    assert card.phone_e164 == "+12145550187"
    assert card.physician_email is None

    entry = db_session.scalars(
        select(CCFpActivityLog).where(
            CCFpActivityLog.referral_id == card.id, CCFpActivityLog.action == "edit_field"
        )
    ).one()
    assert entry.user_id == staff.id
    assert entry.section == "intake_review"
    assert entry.changes == {
        "first_name": {"from": "Jon", "to": "John"},
        "dob": {"from": None, "to": "1970-01-31"},
        "phone": {"from": "214-555-0000", "to": "+12145550187"},
    }


def test_typing_a_phone_in_another_format_is_not_an_edit(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(
        db_session, first_name="Ann", phone="+12145550187", phone_e164="+12145550187"
    )

    response = patch(
        client, auth_headers(client, staff), card.id, row_version=1, phone="(214) 555-0187"
    )

    assert response.status_code == 200
    assert response.json()["changed_fields"] == []
    db_session.refresh(card)
    assert (card.phone, card.phone_e164, card.row_version) == ("+12145550187", "+12145550187", 1)


def test_a_phone_that_cannot_be_normalised_is_kept_as_typed(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(db_session, first_name="Ann", phone="214-555-0000")

    patch(client, auth_headers(client, staff), card.id, row_version=1, phone="(214) 648-3XXX")

    db_session.refresh(card)
    assert (card.phone, card.phone_e164) == ("(214) 648-3XXX", None)


def test_an_explicit_null_clears_a_field(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(
        db_session, first_name="Ann", phone="214-555-0000", phone_e164="+12145550000"
    )

    patch(client, auth_headers(client, staff), card.id, row_version=1, phone=None)

    db_session.refresh(card)
    assert (card.phone, card.phone_e164) == (None, None)


def test_an_edit_that_changes_nothing_is_not_a_new_version(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(db_session, first_name="Ann")

    response = patch(client, auth_headers(client, staff), card.id, row_version=1, first_name="Ann")

    assert response.json() == {"id": card.id, "row_version": 1, "changed_fields": []}
    assert (
        db_session.scalars(
            select(CCFpActivityLog).where(CCFpActivityLog.action == "edit_field")
        ).all()
        == []
    )


def test_a_stale_edit_is_a_conflict(client: TestClient, db_session: Session, staff: Any) -> None:
    card = intake_card(db_session, first_name="Ann", row_version=3)

    response = patch(client, auth_headers(client, staff), card.id, row_version=2, first_name="Bea")

    assert response.status_code == 409
    db_session.refresh(card)
    assert card.first_name == "Ann"


def test_only_review_cards_can_be_edited_here(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = intake_card(db_session, status="ready_for_contact")

    response = patch(client, auth_headers(client, staff), card.id, row_version=1, first_name="X")

    assert response.status_code == 422
    assert "Review Required" in response.json()["detail"]


def test_choosing_clinic_and_exams(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)

    response = patch(
        client,
        headers,
        card.id,
        row_version=1,
        acuity_calendar_id=acuity["dallas"],
        exam_1_id=acuity["carotid"],
        exam_1_fax_text="US CAROTID DOPPLER BILAT",
        exam_2_id=acuity["abd"],
    )

    assert response.status_code == 200
    assert response.json()["changed_fields"] == ["clinic", "exam_1", "exam_2"]

    body = detail(client, headers, card.id)
    assert body["clinic"] == {"acuity_calendar_id": acuity["dallas"], "name": "Dallas"}
    assert body["exam_1"] == {
        "acuity_appointment_type_id": acuity["carotid"],
        "name": "Carotid Doppler Ultrasound",
        "fax_text": "US CAROTID DOPPLER BILAT",
    }
    assert body["exam_2"]["name"] == "Abdominal Ultrasound (Limited)"

    db_session.refresh(card)
    assert card.acuity_calendar_name == "Dallas"


def test_changing_and_clearing_an_exam(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)

    patch(
        client,
        headers,
        card.id,
        row_version=1,
        exam_1_id=acuity["carotid"],
        exam_2_id=acuity["abd"],
    )
    patch(client, headers, card.id, row_version=2, exam_1_id=acuity["scrotal"], exam_2_id=None)

    body = detail(client, headers, card.id)
    assert body["exam_1"]["name"] == "Scrotal Ultrasound"
    assert body["exam_2"] is None
    assert len(db_session.scalars(select(CCFpReferralExam)).all()) == 1


def test_clinics_and_exams_must_exist_and_be_active(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)

    for body in (
        {"acuity_calendar_id": 999},
        {"exam_1_id": 999},
        {"exam_1_id": acuity["retired"]},
    ):
        response = patch(client, headers, card.id, row_version=1, **body)

        assert response.status_code == 422, body
        assert "not available" in response.json()["detail"]


def test_an_already_chosen_exam_survives_acuity_retiring_it(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)
    patch(client, headers, card.id, row_version=1, exam_1_id=acuity["carotid"])
    db_session.get(CCFpAcuityAppointmentType, acuity["carotid"]).is_active = False
    db_session.commit()

    response = patch(
        client, headers, card.id, row_version=2, exam_1_id=acuity["carotid"], first_name="Ann"
    )

    assert response.status_code == 200


def test_edit_then_release_end_to_end(
    client: TestClient, db_session: Session, staff: Any, acuity: dict[str, int]
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)

    refused = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json={"to_status": "ready_for_contact", "row_version": 1, "source": "button"},
        headers=headers,
    )
    assert refused.status_code == 422

    saved = patch(
        client,
        headers,
        card.id,
        row_version=1,
        acuity_calendar_id=acuity["dallas"],
        exam_1_id=acuity["carotid"],
    ).json()

    released = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json={
            "to_status": "ready_for_contact",
            "row_version": saved["row_version"],
            "source": "button",
        },
        headers=headers,
    )

    assert released.status_code == 200
    db_session.refresh(card)
    assert (card.status, card.released_by_user_id) == ("ready_for_contact", staff.id)

    queue = client.get("/fax-portal/intake/queue", headers=headers).json()
    assert card.id not in [i["id"] for i in queue["items"]]


# ---------------------------------------------------------------------
# The PDF
# ---------------------------------------------------------------------


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake bucket: records lookups and serves one PDF."""
    state: dict[str, Any] = {"lookups": 0, "present": True}

    def find(job_id: str) -> str | None:
        state["lookups"] += 1

        return f"{job_id}/product/InboundFax-20261013-154745-001.pdf" if state["present"] else None

    monkeypatch.setattr(gcs_faxes, "find_fax_pdf", find)
    monkeypatch.setattr(gcs_faxes, "download_fax_pdf", lambda uri: PDF)

    return state


def test_pdf_is_streamed_with_no_caching_and_the_view_is_audited(
    client: TestClient, db_session: Session, staff: Any, bucket: dict[str, Any]
) -> None:
    card = intake_card(db_session)

    response = client.get(
        f"/fax-portal/faxes/{card.fax_id}/pdf",
        params={"referral_id": card.id},
        headers=auth_headers(client, staff),
    )

    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("inline")

    entry = db_session.scalars(
        select(CCFpActivityLog).where(CCFpActivityLog.action == "view_pdf")
    ).one()
    assert (entry.user_id, entry.referral_id, entry.fax_id) == (staff.id, card.id, card.fax_id)
    assert entry.section == "intake_review"
    # No PHI in the audit entry: just the size.
    assert entry.details == {"bytes": len(PDF)}


def test_the_pdf_is_linked_on_first_use_and_not_searched_for_again(
    client: TestClient, db_session: Session, staff: Any, bucket: dict[str, Any]
) -> None:
    card = intake_card(db_session)
    headers = auth_headers(client, staff)

    client.get(f"/fax-portal/faxes/{card.fax_id}/pdf", headers=headers)
    client.get(f"/fax-portal/faxes/{card.fax_id}/pdf", headers=headers)

    fax = db_session.get(CCFpFax, card.fax_id)
    db_session.refresh(fax)
    assert (
        fax.gcs_uri
        == f"gs://{gcs_faxes.settings.comprehend_gcs_bucket}/{fax.job_id}/product/InboundFax-20261013-154745-001.pdf"
    )
    assert fax.gcs_received_at is not None
    assert bucket["lookups"] == 1  # the second view used the stored link
    assert detail(client, headers, card.id)["fax"]["has_pdf"] is True

    views = db_session.scalars(
        select(CCFpActivityLog).where(CCFpActivityLog.action == "view_pdf")
    ).all()
    assert len(views) == 2  # every view is logged


def test_a_pdf_that_has_not_arrived_is_a_404_and_is_not_logged_as_viewed(
    client: TestClient, db_session: Session, staff: Any, bucket: dict[str, Any]
) -> None:
    bucket["present"] = False
    card = intake_card(db_session)

    response = client.get(
        f"/fax-portal/faxes/{card.fax_id}/pdf", headers=auth_headers(client, staff)
    )

    assert response.status_code == 404
    assert "not available yet" in response.json()["detail"]
    assert (
        db_session.scalars(
            select(CCFpActivityLog).where(CCFpActivityLog.action == "view_pdf")
        ).all()
        == []
    )
    assert db_session.get(CCFpFax, card.fax_id).gcs_uri is None


def test_a_missing_fax_is_a_404(client: TestClient, db_session: Session, staff: Any) -> None:
    response = client.get("/fax-portal/faxes/9999999/pdf", headers=auth_headers(client, staff))

    assert response.status_code == 404


class _Blob:
    def __init__(self, name: str) -> None:
        self.name = name


def _fake_bucket(monkeypatch: pytest.MonkeyPatch, objects: list[str]) -> list[str]:
    """Fakes the GCS client: lists objects under a prefix. Returns the prefixes asked for."""
    prefixes: list[str] = []

    class Client:
        def list_blobs(self, bucket: str, prefix: str) -> list[_Blob]:
            prefixes.append(prefix)

            return [_Blob(o) for o in objects if o.startswith(prefix)]

    monkeypatch.setattr(gcs_faxes, "_client", lambda: Client())

    return prefixes


def test_pdf_lookup_is_one_exact_prefix_on_the_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    prefixes = _fake_bucket(
        monkeypatch,
        [
            "job-A/prod/InboundFax-20261013-154745-001.pdf",
            "job-B/prod/InboundFax-20261013-162321-001.pdf",
            "job-B/prod/InboundFax-20261013-162321-001.pdf.json",
        ],
    )

    assert gcs_faxes.find_fax_pdf("job-B") == "job-B/prod/InboundFax-20261013-162321-001.pdf"
    assert prefixes == ["job-B/"]  # no scanning of the bucket
    assert gcs_faxes.find_fax_pdf("job-unknown") is None
    # "job-A" must not match "job-AB/...": the prefix includes the slash.
    assert gcs_faxes.find_fax_pdf("job") is None


def test_the_whole_fax_is_preferred_over_split_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_bucket(
        monkeypatch,
        [
            "j/InboundFax-20261013-154745-003.pdf",
            "j/InboundFax-20261013-154745-002.pdf",
            "j/InboundFax-20261013-154745-001.pdf",
        ],
    )

    assert gcs_faxes.find_fax_pdf("j") == "j/InboundFax-20261013-154745-001.pdf"


def test_without_a_whole_fax_file_the_first_pdf_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_bucket(
        monkeypatch,
        ["j/InboundFax-x-002.pdf", "j/InboundFax-x-003.pdf", "j/notes.txt"],
    )

    assert gcs_faxes.find_fax_pdf("j") == "j/InboundFax-x-002.pdf"


def test_an_empty_job_id_never_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    prefixes = _fake_bucket(monkeypatch, ["/stray.pdf"])

    assert gcs_faxes.find_fax_pdf("") is None
    assert prefixes == []


def test_only_the_configured_bucket_is_ever_read(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(gcs_faxes.FaxPdfNotFoundError):
        gcs_faxes.download_fax_pdf("gs://some-other-bucket/secret.pdf")

    assert json.dumps(gcs_faxes.to_gcs_uri("x.pdf")).startswith('"gs://')
