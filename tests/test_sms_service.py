from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.integrations.twilio_client import TwilioApiError, TwilioSendResult
from app.services import sms as sms_service


class FakeSession:
    """Stands in for both `db` and `prod_db` - all repository calls are
    monkeypatched away in these tests, so nothing here needs to be a real
    SQLAlchemy Session, only track commit()/rollback() calls."""

    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


@dataclass
class FakeSmsRow:
    id: int = 1
    destination_number: str = "+15557654321"
    status: str = "submitted"
    twilio_message_sid: str | None = "SM123"
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FakeAppointment:
    appointment_id: str
    first_name: str | None = "Jane"
    last_name: str | None = "Doe"
    phone: str | None = "+15557654321"
    clinic_id: int | None = None
    appointment_type: str | None = "Abdominal Ultrasound"
    category: str | None = None
    appointment_datetime: datetime | None = None


@dataclass
class FakeIntake:
    dob: str | None = "04/12/1990"


@dataclass
class FakeClinic:
    map_link: str | None = None


def _fake_actor() -> Any:
    return SimpleNamespace(id=1)


def _patch_common_send_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    appointment: FakeAppointment | None,
    intake: FakeIntake | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Patches send_sms (Twilio), create_sms_transmission, and the two
    production-write repository functions, recording every call made to
    insert_outbound_message/insert_form_tracking so tests can assert on it.
    """
    calls: dict[str, list[dict[str, Any]]] = {
        "insert_outbound_message": [],
        "insert_form_tracking": [],
    }

    monkeypatch.setattr(
        sms_service,
        "send_sms",
        lambda *, to, body: TwilioSendResult(
            message_sid="SM123",
            from_number="+15550001111",
        ),
    )
    monkeypatch.setattr(
        sms_service,
        "create_sms_transmission",
        lambda db, **kwargs: FakeSmsRow(),
    )
    monkeypatch.setattr(
        sms_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: appointment,
    )
    monkeypatch.setattr(
        sms_service,
        "get_patient_intake",
        lambda prod_db, appointment_id: intake,
    )

    def fake_insert_outbound_message(prod_db, **kwargs) -> None:
        calls["insert_outbound_message"].append(kwargs)

    def fake_insert_form_tracking(prod_db, **kwargs) -> None:
        calls["insert_form_tracking"].append(kwargs)

    monkeypatch.setattr(sms_service, "insert_outbound_message", fake_insert_outbound_message)
    monkeypatch.setattr(sms_service, "insert_form_tracking", fake_insert_form_tracking)

    return calls


def test_successful_form_purpose_send_records_message_and_form_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(
        appointment_id="APT-1",
        appointment_datetime=datetime(2026, 9, 20, 14, 30, tzinfo=UTC),
    )
    calls = _patch_common_send_path(
        monkeypatch,
        appointment=appointment,
        intake=FakeIntake(dob="04/12/1990"),
    )

    db = FakeSession()
    prod_db = FakeSession()

    result = sms_service.send_patient_sms(
        db,
        prod_db,
        appointment_id="APT-1",
        purpose="pcp_form",
        destination_number="+15557654321",
        body="Here is your PCP form link.",
        actor=_fake_actor(),
    )

    assert result.status == "submitted"

    assert len(calls["insert_outbound_message"]) == 1
    message_call = calls["insert_outbound_message"][0]
    assert message_call["appointment_id"] == "APT-1"
    assert message_call["status"] == "sent"
    assert message_call["message_sid"] == "SM123"
    assert message_call["sender"] == "+15550001111"
    assert message_call["recipient"] == "+15557654321"

    assert len(calls["insert_form_tracking"]) == 1
    form_call = calls["insert_form_tracking"][0]
    assert form_call["appointment_id"] == "APT-1"
    assert form_call["form_type"] == "pcp_declaration"
    assert form_call["patient_name"] == "Jane Doe"
    assert form_call["dob"] == "04-12-1990"
    assert form_call["appointment_date"] == "09-20-2026"
    assert form_call["appointment_type"] == "Abdominal Ultrasound"

    # Both best-effort writes committed independently.
    assert prod_db.committed == 2
    assert prod_db.rolled_back == 0


@pytest.mark.parametrize(
    ("purpose", "expected_form_type"),
    [
        ("scrotal_form", "scrotal_consent"),
        ("transvag_form", "transvag_consent"),
    ],
)
def test_other_form_purposes_map_to_correct_form_type(
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
    expected_form_type: str,
) -> None:
    appointment = FakeAppointment(appointment_id="APT-2")
    calls = _patch_common_send_path(monkeypatch, appointment=appointment, intake=FakeIntake())

    sms_service.send_patient_sms(
        FakeSession(),
        FakeSession(),
        appointment_id="APT-2",
        purpose=purpose,
        destination_number="+15557654321",
        body="form link",
        actor=_fake_actor(),
    )

    assert len(calls["insert_form_tracking"]) == 1
    assert calls["insert_form_tracking"][0]["form_type"] == expected_form_type


def test_non_form_purpose_records_message_but_not_form_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(appointment_id="APT-3")
    calls = _patch_common_send_path(monkeypatch, appointment=appointment)

    sms_service.send_patient_sms(
        FakeSession(),
        FakeSession(),
        appointment_id="APT-3",
        purpose="waiting",
        destination_number="+15557654321",
        body="Hi, are you here yet?",
        actor=_fake_actor(),
    )

    assert len(calls["insert_outbound_message"]) == 1
    assert len(calls["insert_form_tracking"]) == 0


def test_failed_twilio_send_skips_both_production_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_common_send_path(
        monkeypatch, appointment=FakeAppointment(appointment_id="APT-4")
    )

    monkeypatch.setattr(
        sms_service,
        "send_sms",
        lambda *, to, body: (_ for _ in ()).throw(TwilioApiError("Twilio rejected it")),
    )

    with pytest.raises(HTTPException) as exc_info:
        sms_service.send_patient_sms(
            FakeSession(),
            FakeSession(),
            appointment_id="APT-4",
            purpose="pcp_form",
            destination_number="+15557654321",
            body="form link",
            actor=_fake_actor(),
        )

    assert exc_info.value.status_code == 502
    assert len(calls["insert_outbound_message"]) == 0
    assert len(calls["insert_form_tracking"]) == 0


def test_production_write_failure_is_swallowed_and_does_not_fail_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bookkeeping failure writing to production must never turn a
    successfully-sent SMS into a user-facing error."""
    _patch_common_send_path(monkeypatch, appointment=FakeAppointment(appointment_id="APT-5"))

    def failing_insert_outbound_message(prod_db, **kwargs):
        raise RuntimeError("production db is unreachable")

    monkeypatch.setattr(sms_service, "insert_outbound_message", failing_insert_outbound_message)

    prod_db = FakeSession()

    result = sms_service.send_patient_sms(
        FakeSession(),
        prod_db,
        appointment_id="APT-5",
        purpose="waiting",
        destination_number="+15557654321",
        body="Hi, are you here yet?",
        actor=_fake_actor(),
    )

    assert result.status == "submitted"
    assert prod_db.rolled_back == 1


def test_form_tracking_skipped_when_appointment_lookup_fails_for_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the appointment can't be found during the form_tracking lookup
    step, that write is silently skipped rather than raising - the SMS
    itself already succeeded by this point."""
    calls = _patch_common_send_path(monkeypatch, appointment=None)

    result = sms_service.send_patient_sms(
        FakeSession(),
        FakeSession(),
        appointment_id="APT-MISSING",
        purpose="pcp_form",
        destination_number="+15557654321",
        body="form link",
        actor=_fake_actor(),
    )

    assert result.status == "submitted"
    assert len(calls["insert_form_tracking"]) == 0


# --- get_sms_prefill --------------------------------------------------------


def test_get_sms_prefill_includes_directions_and_google_review_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(appointment_id="APT-6", clinic_id=2, phone="+15551112222")

    monkeypatch.setattr(
        sms_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: appointment,
    )
    monkeypatch.setattr(
        sms_service,
        "get_clinic",
        lambda prod_db, clinic_id: FakeClinic(map_link="https://maps.example/clinic-2"),
    )

    captured: dict[str, Any] = {}

    def fake_get_google_review_url(db, clinic_id: int) -> str:
        captured["clinic_id"] = clinic_id
        return "https://g.page/r/Cbc0EDh29KduEBM/review"

    monkeypatch.setattr(sms_service, "get_google_review_url", fake_get_google_review_url)

    result = sms_service.get_sms_prefill(FakeSession(), FakeSession(), "APT-6")

    assert result.phone == "+15551112222"
    assert result.directions_link == "https://maps.example/clinic-2"
    assert result.google_review_link == "https://g.page/r/Cbc0EDh29KduEBM/review"
    assert captured["clinic_id"] == 2


def test_get_sms_prefill_returns_all_none_when_appointment_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sms_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: None,
    )

    result = sms_service.get_sms_prefill(FakeSession(), FakeSession(), "APT-404")

    assert result.phone is None
    assert result.directions_link is None
    assert result.google_review_link is None


def test_get_sms_prefill_skips_clinic_lookups_when_no_clinic_on_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appointment = FakeAppointment(appointment_id="APT-7", clinic_id=None)

    monkeypatch.setattr(
        sms_service,
        "get_appointment_by_appointment_id",
        lambda prod_db, appointment_id: appointment,
    )

    def unexpectedly_called(*args, **kwargs):
        raise AssertionError("clinic lookup should not run without a clinic_id")

    monkeypatch.setattr(sms_service, "get_clinic", unexpectedly_called)
    monkeypatch.setattr(sms_service, "get_google_review_url", unexpectedly_called)

    result = sms_service.get_sms_prefill(FakeSession(), FakeSession(), "APT-7")

    assert result.directions_link is None
    assert result.google_review_link is None
