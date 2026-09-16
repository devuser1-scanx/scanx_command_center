from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.integrations.acuity_client import AcuityApiError
from app.services import reschedule_links as reschedule_links_service


def test_create_reschedule_link_for_appointment_returns_acuity_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_get_reschedule_link(*, appointment_id: str) -> str:
        captured["appointment_id"] = appointment_id
        return "https://acuityscheduling.com/schedule.php?owner=1&confirmed=APT-1"

    monkeypatch.setattr(reschedule_links_service, "get_reschedule_link", fake_get_reschedule_link)

    result = reschedule_links_service.create_reschedule_link_for_appointment(
        appointment_id="APT-1",
    )

    assert captured["appointment_id"] == "APT-1"
    assert result.url == "https://acuityscheduling.com/schedule.php?owner=1&confirmed=APT-1"


def test_create_reschedule_link_for_appointment_502_when_acuity_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_get_reschedule_link(*, appointment_id: str) -> str:
        raise AcuityApiError("Appointment not found")

    monkeypatch.setattr(
        reschedule_links_service, "get_reschedule_link", failing_get_reschedule_link
    )

    with pytest.raises(HTTPException) as exc_info:
        reschedule_links_service.create_reschedule_link_for_appointment(
            appointment_id="APT-404",
        )

    assert exc_info.value.status_code == 502
