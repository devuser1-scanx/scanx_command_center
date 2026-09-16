from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services import transvag_form_links as transvag_form_links_service


@dataclass
class FakeAppointment:
    appointment_id: str
    first_name: str | None
    last_name: str | None


@dataclass
class FakeIntake:
    dob: str | None


def test_create_transvag_form_link_for_appointment_builds_prefilled_url(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(appointment_id="APT-1", first_name="Jane", last_name="Doe")

    monkeypatch.setattr(
        transvag_form_links_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: appointment,
    )
    monkeypatch.setattr(
        transvag_form_links_service,
        "get_patient_intake",
        lambda prod_db, appointment_id: FakeIntake(dob="04/12/1990"),
    )

    result = transvag_form_links_service.create_transvag_form_link_for_appointment(
        db_session,
        appointment_id="APT-1",
    )

    assert result.url == (
        "https://form.jotform.com/261391142398056"
        "?q4_textbox2=Jane%20Doe&dateOf=04-12-1990&appointmentId=APT-1"
    )
    assert "q3_textbox1" not in result.url
    assert "appointmentDate" not in result.url


def test_create_transvag_form_link_for_appointment_404_when_appointment_not_found(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transvag_form_links_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        transvag_form_links_service.create_transvag_form_link_for_appointment(
            db_session,
            appointment_id="APT-404",
        )

    assert exc_info.value.status_code == 404
