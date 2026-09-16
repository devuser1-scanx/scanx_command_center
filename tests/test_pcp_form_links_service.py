from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services import pcp_form_links as pcp_form_links_service


@dataclass
class FakeAppointment:
    appointment_id: str
    first_name: str | None
    last_name: str | None
    appointment_datetime: datetime | None


@dataclass
class FakeIntake:
    dob: str | None


def test_create_pcp_form_link_for_appointment_builds_prefilled_url(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(
        appointment_id="APT-1",
        first_name="Jane",
        last_name="Doe",
        appointment_datetime=datetime(2026, 9, 20, 14, 30, tzinfo=UTC),
    )

    monkeypatch.setattr(
        pcp_form_links_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: appointment,
    )
    monkeypatch.setattr(
        pcp_form_links_service,
        "get_patient_intake",
        lambda prod_db, appointment_id: FakeIntake(dob="04/12/1990"),
    )

    result = pcp_form_links_service.create_pcp_form_link_for_appointment(
        db_session,
        appointment_id="APT-1",
    )

    assert result.url == (
        "https://form.jotform.com/261322983766062"
        "?q3_textbox1=Jane%20Doe&dateOf=04-12-1990&appointmentDate=09-20-2026&appointmentId=APT-1"
    )


def test_create_pcp_form_link_for_appointment_handles_missing_intake(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(
        appointment_id="APT-2",
        first_name="John",
        last_name="Smith",
        appointment_datetime=None,
    )

    monkeypatch.setattr(
        pcp_form_links_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: appointment,
    )
    monkeypatch.setattr(
        pcp_form_links_service,
        "get_patient_intake",
        lambda prod_db, appointment_id: None,
    )

    result = pcp_form_links_service.create_pcp_form_link_for_appointment(
        db_session,
        appointment_id="APT-2",
    )

    assert "dateOf=&" in result.url
    assert "appointmentDate=&" in result.url


def test_create_pcp_form_link_for_appointment_404_when_appointment_not_found(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pcp_form_links_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        pcp_form_links_service.create_pcp_form_link_for_appointment(
            db_session,
            appointment_id="APT-404",
        )

    assert exc_info.value.status_code == 404
