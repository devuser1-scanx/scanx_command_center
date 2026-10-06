from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.repositories import production_writes
from app.services import manual_checkin as svc


class FakeResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value


class FakeProdDb:
    """Returns the appointment for the locked SELECT and a Checkin row for
    the upsert, and counts calls."""

    def __init__(self, appointment: Any) -> None:
        self.appointment = appointment
        self.checkin_row = SimpleNamespace(checkin_time=None)
        self.executed = 0
        self.flushed = 0
        self.committed = 0
        self.rolled_back = 0

    def execute(self, statement: Any) -> FakeResult:
        self.executed += 1
        return FakeResult(self.appointment if self.executed == 1 else self.checkin_row)

    def flush(self) -> None:
        self.flushed += 1

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def make_appointment(**overrides: Any) -> SimpleNamespace:
    values = {
        "appointment_id": "APPT1",
        "checkin": False,
        "checked_in_at": None,
        "updated_at": None,
        "clinic_id": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------- repository


def test_repository_returns_none_when_appointment_missing() -> None:
    db = FakeProdDb(None)

    assert (
        production_writes.manual_check_in_appointment(db, appointment_id="X", location=None)
        is None
    )


def test_repository_checks_in_an_unchecked_appointment() -> None:
    appointment = make_appointment()
    db = FakeProdDb(appointment)
    when = datetime(2026, 10, 6, 15, 0)

    result = production_writes.manual_check_in_appointment(
        db, appointment_id="APPT1", location="Dallas", checked_in_at=when
    )

    assert result.already_checked_in is False
    assert appointment.checkin is True
    assert appointment.checked_in_at == when
    assert appointment.updated_at == when


def test_repository_rechecks_in_an_already_checked_in_appointment() -> None:
    """There is no already-checked-in guard: a second call rewrites the
    check-in values instead of returning early."""
    appointment = make_appointment(checkin=True, checked_in_at=datetime(2026, 10, 6, 9, 0))
    db = FakeProdDb(appointment)
    when = datetime(2026, 10, 6, 15, 0)

    result = production_writes.manual_check_in_appointment(
        db, appointment_id="APPT1", location="Dallas", checked_in_at=when
    )

    assert result.already_checked_in is False
    assert result.checked_in_at == when
    assert appointment.checked_in_at == when
    assert db.executed == 2  # locked SELECT, then the Checkin upsert


def test_repository_normalises_aware_timestamps_to_naive_utc() -> None:
    appointment = make_appointment()
    db = FakeProdDb(appointment)

    production_writes.manual_check_in_appointment(
        db,
        appointment_id="APPT1",
        location=None,
        checked_in_at=datetime(2026, 10, 6, 15, 0, tzinfo=UTC),
    )

    assert appointment.checked_in_at == datetime(2026, 10, 6, 15, 0)
    assert appointment.checked_in_at.tzinfo is None


# ------------------------------------------------------------------- service


def test_commit_manual_checkin_commits_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeProdDb(None)
    result = SimpleNamespace(already_checked_in=False)
    monkeypatch.setattr(svc, "manual_check_in_appointment", lambda *a, **k: result)

    assert svc._commit_manual_checkin(db, appointment_id="APPT1", location=None) is result
    assert db.committed == 1


def test_commit_manual_checkin_404_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeProdDb(None)
    monkeypatch.setattr(svc, "manual_check_in_appointment", lambda *a, **k: None)

    with pytest.raises(HTTPException) as exc:
        svc._commit_manual_checkin(db, appointment_id="APPT1", location=None)

    assert exc.value.status_code == 404
    assert db.rolled_back == 1
    assert db.committed == 0


def test_commit_manual_checkin_wraps_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeProdDb(None)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise ValueError("db down")

    monkeypatch.setattr(svc, "manual_check_in_appointment", boom)

    with pytest.raises(HTTPException) as exc:
        svc._commit_manual_checkin(db, appointment_id="APPT1", location=None)

    assert exc.value.status_code == 500
    assert db.rolled_back == 1


def test_manual_check_in_patient_always_runs_every_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the appointment is already flagged as checked in, the full
    flow runs and the response reports a fresh check-in."""
    appointment = make_appointment(checkin=True, clinic_id=1)
    checked_in_at = datetime(2026, 10, 6, 15, 0)
    calls: list[str] = []

    monkeypatch.setattr(svc, "get_appointment_by_appointment_id", lambda db, i: appointment)
    monkeypatch.setattr(
        svc, "get_clinic", lambda db, i: SimpleNamespace(name="Dallas", timezone="America/Chicago")
    )
    monkeypatch.setattr(
        svc,
        "_commit_manual_checkin",
        lambda *a, **k: SimpleNamespace(
            appointment=appointment, checked_in_at=checked_in_at, already_checked_in=False
        ),
    )
    monkeypatch.setattr(
        svc, "_record_audit_best_effort", lambda *a, **k: calls.append("audit") or "recorded"
    )
    monkeypatch.setattr(
        svc, "_update_acuity_best_effort", lambda *a, **k: calls.append("acuity") or "updated"
    )
    monkeypatch.setattr(
        svc, "_send_google_chat_best_effort", lambda *a, **k: calls.append("chat") or "sent"
    )
    monkeypatch.setattr(
        svc,
        "_send_checkin_confirmation_sms_best_effort",
        lambda *a, **k: calls.append("sms") or "submitted",
    )
    monkeypatch.setattr(
        svc,
        "_send_payment_reminder_rcs_best_effort",
        lambda *a, **k: calls.append("rcs") or "submitted",
    )

    response = svc.manual_check_in_patient(
        object(),
        object(),
        appointment_id="APPT1",
        actor=SimpleNamespace(id=1),
        ip_address=None,
        user_agent=None,
    )

    assert calls == ["audit", "acuity", "chat", "sms", "rcs"]
    assert response.message == "Patient checked in successfully."
    assert response.already_checked_in is False
    assert response.integrations.google_chat == "sent"


def test_manual_check_in_patient_404_when_appointment_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "get_appointment_by_appointment_id", lambda db, i: None)

    with pytest.raises(HTTPException) as exc:
        svc.manual_check_in_patient(
            object(),
            object(),
            appointment_id="NOPE",
            actor=SimpleNamespace(id=1),
            ip_address=None,
            user_agent=None,
        )

    assert exc.value.status_code == 404
