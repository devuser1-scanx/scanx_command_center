from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from conftest import ensure_role
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.routes.ws_calls import CallsConnectionManager
from app.core.config import settings
from app.db.prod_session import get_prod_db
from app.main import app
from app.models.auth import CCUserActivityAudit
from app.models.calls import CCCallLog
from app.repositories import calls as calls_repository
from app.repositories import production_writes
from app.repositories.calls import day_range_utc, format_call_row, resolve_days
from app.services import dialer_calls

PATIENT = "+12145550187"
KEY_SID = "SKunittestkey"
KEY_SECRET = "unit-test-api-key-secret-0123456789"
FAX_APP_SID = "APunittestfaxportal"
# The standalone Web Dialer's TwiML App: the one Calls tokens name.
DIALER_APP_SID = "APunittestdialer"


@pytest.fixture(autouse=True)
def voice_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_api_key_sid", KEY_SID)
    monkeypatch.setattr(settings, "twilio_api_key_secret", KEY_SECRET)
    monkeypatch.setattr(settings, "twilio_twiml_app_sid", FAX_APP_SID)
    monkeypatch.setattr(settings, "dialer_twiml_app_sid", DIALER_APP_SID)


@pytest.fixture(autouse=True)
def no_prod_database():
    """The production database is not used by these tests: reads are stubbed per
    test and the writes go to FakeProd."""
    app.dependency_overrides[get_prod_db] = lambda: SimpleNamespace(
        commit=lambda: None, close=lambda: None
    )
    yield
    app.dependency_overrides.pop(get_prod_db, None)


class FakeProd:
    """Stands in for the production tables: what the standalone Web Dialer wrote
    for each call (`events`, `states`) and what Command Center writes (notes)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.states: dict[int, dict[str, Any]] = {}
        self.notes: list[dict[str, Any]] = []
        self.dispositions: list[dict[str, Any]] = []
        self.looked_up = 0

    def find_outgoing_events(self, prod_db: Any, cc_call_ids: list[int]) -> list[dict[str, Any]]:
        self.looked_up += 1
        wanted = {str(call_id) for call_id in cc_call_ids}

        return [event for event in self.events if event["cc_call_id"] in wanted]

    def get_call_log_states(self, prod_db: Any, call_log_ids: list[int]) -> dict[int, dict]:
        return {i: self.states[i] for i in call_log_ids if i in self.states}

    def insert_call_note(self, prod_db: Any, **kwargs: Any) -> int:
        self.notes.append(kwargs)

        return 71

    def insert_call_disposition(self, prod_db: Any, **kwargs: Any) -> int:
        self.dispositions.append(kwargs)

        return 72

    def standalone_saw(self, call: CCCallLog, *, log_id: int = 501, caller: str | None = None):
        """The standalone service handled this call: it logged the row and kept
        Twilio's request (with the CcCallId the browser sent) as an event."""
        self.events.append(
            {
                "call_log_id": log_id,
                "cc_call_id": str(call.id),
                "caller": caller or f"client:user-{call.user_id}",
            }
        )
        self.states.setdefault(log_id, {"call_sid": f"CA{log_id}", "status": "initiated"})


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> FakeProd:
    fake = FakeProd()

    monkeypatch.setattr(calls_repository, "find_outgoing_events", fake.find_outgoing_events)
    monkeypatch.setattr(calls_repository, "get_call_log_states", fake.get_call_log_states)
    monkeypatch.setattr(production_writes, "insert_call_note", fake.insert_call_note)
    monkeypatch.setattr(production_writes, "insert_call_disposition", fake.insert_call_disposition)

    return fake


@pytest.fixture
def agent(db_session: Session) -> Any:
    return make_user(db_session, "sales")


def make_call(db: Session, user: Any, **kwargs: Any) -> CCCallLog:
    kwargs.setdefault("to_number", PATIENT)
    kwargs.setdefault("status", "queued")
    call = CCCallLog(user_id=user.id, direction="outbound", **kwargs)
    db.add(call)
    db.commit()

    return call


def fresh(db: Session, call: CCCallLog) -> CCCallLog:
    db.expire_all()

    return db.get(CCCallLog, call.id)


def actions(db: Session) -> list[str]:
    return list(db.scalars(select(CCUserActivityAudit.action).order_by(CCUserActivityAudit.id)))


def sync(db: Session) -> int:
    return dialer_calls.sync_open_calls(db, SimpleNamespace())


# ---------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------


def test_every_endpoint_requires_login(client: TestClient) -> None:
    for path in (
        "/calls/summary",
        "/calls/recent",
        "/calls/missed",
        "/calls/patients/search?q=a",
        "/calls/patients/call-history?phone=555",
    ):
        assert client.get(path).status_code == 401, path

    assert client.post("/calls/dial", json={"number": PATIENT}).status_code == 401
    assert client.post("/calls/1/ended", json={"reached_twilio": True}).status_code == 401


def test_a_user_without_calls_permissions_is_refused(
    client: TestClient, db_session: Session
) -> None:
    ensure_role(db_session, code="nocalls", name="No Calls")
    db_session.commit()
    headers = auth_headers(client, make_user(db_session, "nocalls"))

    assert client.get("/calls/summary", headers=headers).status_code == 403
    assert client.get("/calls/recent", headers=headers).status_code == 403
    assert client.post("/calls/dial", json={"number": PATIENT}, headers=headers).status_code == 403
    assert client.post("/calls/1/notes", json={"note": "x"}, headers=headers).status_code == 403
    assert (
        client.post("/calls/1/ended", json={"reached_twilio": True}, headers=headers).status_code
        == 403
    )


# ---------------------------------------------------------------------
# Starting a call
# ---------------------------------------------------------------------


def test_dialling_logs_the_call_and_returns_what_the_browser_connects_with(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    response = client.post(
        "/calls/dial",
        json={"number": "(214) 555-0187", "appointment_id": "A-1"},
        headers=auth_headers(client, agent),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    [call] = db_session.scalars(select(CCCallLog)).all()
    assert body["call_id"] == call.id
    # The number is validated and turned into E.164 before the browser gets it.
    assert body["to"] == PATIENT
    assert (call.user_id, call.to_number, call.appointment_id) == (agent.id, PATIENT, "A-1")
    assert (call.status, call.twilio_call_sid, call.direction) == ("queued", None, "outbound")

    claims = jwt.decode(
        body["token"], KEY_SECRET, algorithms=["HS256"], options={"verify_aud": False}
    )
    assert (claims["iss"], claims["sub"]) == (KEY_SID, "ACtest")
    assert claims["grants"]["identity"] == f"user-{agent.id}"
    # The token names the standalone Web Dialer's TwiML App - not the Fax Portal's -
    # so Twilio asks the standalone service what to do with the call.
    assert claims["grants"]["voice"] == {"outgoing": {"application_sid": DIALER_APP_SID}}
    assert PATIENT not in str(claims)


def test_dialling_is_audited_without_the_number(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    client.post("/calls/dial", json={"number": PATIENT}, headers=auth_headers(client, agent))

    audit = db_session.scalar(
        select(CCUserActivityAudit).where(CCUserActivityAudit.action == "call.requested")
    )
    assert audit is not None and audit.actor_user_id == agent.id
    assert audit.resource_type == "call"
    assert PATIENT not in str(audit.details)


@pytest.mark.parametrize("number", ["abc", "123", "(214) 555-XXXX", "+1 214 5"])
def test_an_invalid_number_is_refused(
    client: TestClient, db_session: Session, agent: Any, number: str
) -> None:
    response = client.post(
        "/calls/dial", json={"number": number}, headers=auth_headers(client, agent)
    )

    assert response.status_code == 422
    assert db_session.scalars(select(CCCallLog)).all() == []


@pytest.mark.parametrize("missing", ["dialer_twiml_app_sid", "twilio_api_key_secret"])
def test_calling_that_is_not_set_up_answers_503(
    client: TestClient,
    db_session: Session,
    agent: Any,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setattr(settings, missing, None)

    response = client.post(
        "/calls/dial", json={"number": PATIENT}, headers=auth_headers(client, agent)
    )

    assert response.status_code == 503
    assert db_session.scalars(select(CCCallLog)).all() == []


def test_the_fax_portal_app_is_not_enough_for_calls(
    client: TestClient, db_session: Session, agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the Fax Portal's TwiML App is set: Calls has its own variable.
    monkeypatch.setattr(settings, "dialer_twiml_app_sid", None)

    response = client.post(
        "/calls/dial", json={"number": PATIENT}, headers=auth_headers(client, agent)
    )

    assert response.status_code == 503


def test_an_old_call_that_never_reached_twilio_is_cleared(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    stale = make_call(db_session, agent, started_at=datetime.now(UTC) - timedelta(minutes=10))

    response = client.post(
        "/calls/dial", json={"number": PATIENT}, headers=auth_headers(client, agent)
    )

    assert response.status_code == 200
    assert fresh(db_session, stale).status == "abandoned"


def test_a_second_call_is_not_blocked_by_the_first(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    # The standalone service may leave a finished call's status unchanged, so
    # nothing here may stop the next call because the last one looks "active".
    make_call(db_session, agent, status="initiated", twilio_call_sid="CAold")

    response = client.post(
        "/calls/dial", json={"number": PATIENT}, headers=auth_headers(client, agent)
    )

    assert response.status_code == 200


# ---------------------------------------------------------------------
# The browser reporting its call over
# ---------------------------------------------------------------------


def test_a_call_that_never_reached_twilio_is_abandoned(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    call = make_call(db_session, agent)
    headers = auth_headers(client, agent)

    first = client.post(f"/calls/{call.id}/ended", json={"reached_twilio": False}, headers=headers)
    again = client.post(f"/calls/{call.id}/ended", json={"reached_twilio": False}, headers=headers)

    assert first.status_code == again.status_code == 204
    assert fresh(db_session, call).status == "abandoned"
    assert actions(db_session).count("call.abandoned") == 1


def test_the_end_of_a_connected_call_is_recorded_once(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    call = make_call(db_session, agent, status="initiated", twilio_call_sid="CAx")
    headers = auth_headers(client, agent)

    client.post(
        f"/calls/{call.id}/ended", json={"reached_twilio": True, "duration_s": 61}, headers=headers
    )
    client.post(
        f"/calls/{call.id}/ended", json={"reached_twilio": True, "duration_s": 99}, headers=headers
    )

    call = fresh(db_session, call)
    assert call.ended_at is not None and call.duration_s == 61
    assert actions(db_session).count("call.ended") == 1


def test_only_the_callers_own_call_can_be_ended(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    theirs = make_call(db_session, make_user(db_session, "sales"))
    headers = auth_headers(client, agent)

    assert (
        client.post(
            f"/calls/{theirs.id}/ended", json={"reached_twilio": False}, headers=headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/calls/999999/ended", json={"reached_twilio": False}, headers=headers
        ).status_code
        == 404
    )
    assert fresh(db_session, theirs).status == "queued"


def test_a_silly_duration_is_refused(client: TestClient, db_session: Session, agent: Any) -> None:
    call = make_call(db_session, agent)

    response = client.post(
        f"/calls/{call.id}/ended",
        json={"reached_twilio": True, "duration_s": -5},
        headers=auth_headers(client, agent),
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# Linking to, and following, the standalone service's production record
# ---------------------------------------------------------------------


def test_a_call_is_linked_to_the_production_row_the_standalone_service_wrote(
    db_session: Session, agent: Any, prod: FakeProd
) -> None:
    call = make_call(db_session, agent)
    prod.standalone_saw(call, log_id=501)

    assert sync(db_session) == 1

    call = fresh(db_session, call)
    assert call.prod_call_log_id == 501
    assert (call.twilio_call_sid, call.status) == ("CA501", "initiated")


def test_a_call_made_by_someone_else_cannot_be_claimed(
    db_session: Session, agent: Any, prod: FakeProd
) -> None:
    other = make_user(db_session, "sales")
    call = make_call(db_session, agent)
    # A request that quotes this call's id but came from another person's token.
    prod.standalone_saw(call, caller=f"client:user-{other.id}")

    sync(db_session)

    call = fresh(db_session, call)
    assert call.prod_call_log_id is None and call.status == "queued"


def test_the_call_follows_the_production_status_and_is_audited_when_it_ends(
    db_session: Session, agent: Any, prod: FakeProd
) -> None:
    call = make_call(db_session, agent)
    prod.standalone_saw(call, log_id=501)
    sync(db_session)

    prod.states[501].update(status="in-progress")
    sync(db_session)
    call = fresh(db_session, call)
    assert call.status == "in-progress" and call.answered_at is not None

    prod.states[501].update(status="completed", duration=42)
    sync(db_session)
    sync(db_session)

    call = fresh(db_session, call)
    assert (call.status, call.duration_s) == ("completed", 42)
    assert call.ended_at is not None
    assert actions(db_session).count("call.ended") == 1


@pytest.mark.parametrize("outcome", ["no-answer", "busy", "failed", "canceled", "cancelled"])
def test_a_call_that_ended_without_a_conversation_is_recorded_as_such(
    db_session: Session, agent: Any, prod: FakeProd, outcome: str
) -> None:
    call = make_call(db_session, agent)
    prod.standalone_saw(call, log_id=501)
    prod.states[501].update(status=outcome, duration=0)

    sync(db_session)

    call = fresh(db_session, call)
    assert call.status == ("canceled" if outcome == "cancelled" else outcome)
    assert call.ended_at is not None


def test_a_status_never_moves_backwards(db_session: Session, agent: Any, prod: FakeProd) -> None:
    call = make_call(db_session, agent)
    prod.standalone_saw(call, log_id=501)
    prod.states[501].update(status="in-progress")
    sync(db_session)

    prod.states[501].update(status="initiated")
    sync(db_session)

    assert fresh(db_session, call).status == "in-progress"


def test_the_browsers_report_and_the_production_status_do_not_double_audit(
    client: TestClient, db_session: Session, agent: Any, prod: FakeProd
) -> None:
    call = make_call(db_session, agent)
    prod.standalone_saw(call, log_id=501)
    sync(db_session)

    client.post(
        f"/calls/{call.id}/ended",
        json={"reached_twilio": True, "duration_s": 30},
        headers=auth_headers(client, agent),
    )
    prod.states[501].update(status="completed", duration=28)
    sync(db_session)

    call = fresh(db_session, call)
    # The standalone service's figures win once it has them.
    assert (call.status, call.duration_s) == ("completed", 28)
    assert actions(db_session).count("call.ended") == 1


def test_a_call_that_never_reached_the_standalone_service_is_dropped(
    db_session: Session, agent: Any, prod: FakeProd
) -> None:
    call = make_call(db_session, agent, started_at=datetime.now(UTC) - timedelta(minutes=5))

    sync(db_session)

    assert fresh(db_session, call).status == "abandoned"


def test_a_sid_already_on_another_call_is_not_reused(
    db_session: Session, agent: Any, prod: FakeProd
) -> None:
    make_call(db_session, agent, status="initiated", twilio_call_sid="CA501", ended_at=None)
    call = make_call(db_session, agent)
    prod.standalone_saw(call, log_id=501)

    sync(db_session)

    assert fresh(db_session, call).twilio_call_sid is None


def test_calls_that_are_long_over_are_left_alone(
    db_session: Session, agent: Any, prod: FakeProd
) -> None:
    now = datetime.now(UTC)
    make_call(db_session, agent, status="initiated", started_at=now - timedelta(hours=7))
    make_call(
        db_session,
        agent,
        status="initiated",
        started_at=now - timedelta(hours=1),
        ended_at=now - timedelta(minutes=30),
    )
    make_call(db_session, agent, status="completed")

    assert sync(db_session) == 0
    # Nothing to follow: the production database was not even asked.
    assert prod.looked_up == 0


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


def call_row(**kwargs: Any) -> dict[str, Any]:
    return format_call_row(
        {
            "id": 1,
            "direction": "outbound",
            "status": "completed",
            "patient_number": PATIENT,
            "created_at": datetime(2026, 10, 28, 15, 0),
            **kwargs,
        }
    )


def test_the_summary_covers_the_requested_clinic_days(
    client: TestClient, db_session: Session, agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_summary(prod_db: Any, start: datetime, end: datetime) -> dict[str, Any]:
        seen.update(start=start, end=end)

        return {
            "total_calls": 2,
            "outgoing_calls": 2,
            "incoming_calls": 0,
            "missed_calls": 1,
            "completed_calls": 1,
            "failed_or_busy_calls": 0,
            "no_answer_calls": 1,
            "average_duration_seconds": 30,
            "recent_calls": [call_row()],
            "missed_calls_list": [call_row(id=2, status="no-answer")],
        }

    monkeypatch.setattr(calls_repository, "call_summary", fake_summary)

    response = client.get(
        "/calls/summary?start_date=2026-10-01&end_date=2026-10-03",
        headers=auth_headers(client, agent),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["start_date"], body["end_date"], body["total_calls"]) == (
        "2026-10-01",
        "2026-10-03",
        2,
    )
    assert body["missed_calls_list"][0]["is_missed"] is True
    assert body["recent_calls"][0]["display_name"] == "Unknown Caller"
    # Clinic (Chicago) days, as UTC: Oct 1 00:00 CDT .. Oct 3 23:59 CDT.
    assert seen["start"] == datetime(2026, 10, 1, 5, 0)
    assert seen["end"].date() == date(2026, 10, 4)


def test_bad_parameters_are_refused(client: TestClient, agent: Any) -> None:
    headers = auth_headers(client, agent)

    assert client.get("/calls/summary?start_date=yesterday", headers=headers).status_code == 422
    assert client.get("/calls/summary?start_date=2026-13-45", headers=headers).status_code == 422
    assert client.get("/calls/recent?limit=0", headers=headers).status_code == 422
    assert client.get("/calls/recent?limit=5000", headers=headers).status_code == 422
    assert client.get("/calls/patients/search?q=", headers=headers).status_code == 422


def test_recent_and_missed_calls(
    client: TestClient, agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calls_repository, "list_recent_calls", lambda prod_db, limit: [call_row()])
    monkeypatch.setattr(
        calls_repository,
        "list_missed_calls",
        lambda prod_db, limit: [call_row(id=2, status="busy", patient_name="Pat Doe")],
    )
    headers = auth_headers(client, agent)

    recent = client.get("/calls/recent", headers=headers).json()
    missed = client.get("/calls/missed", headers=headers).json()

    assert recent["count"] == 1 and recent["results"][0]["created_at"].endswith(("Z", "+00:00"))
    assert missed["results"][0]["display_name"] == "Pat Doe"
    assert missed["results"][0]["is_known_patient"] is True


def test_patient_search_and_history(
    client: TestClient, agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    patient = {
        "appointment_id": "A-1",
        "full_name": "Pat Doe",
        "phone": PATIENT,
        "date": "2026-10-28",
    }
    monkeypatch.setattr(
        calls_repository, "search_patients", lambda prod_db, query, limit: [patient]
    )
    monkeypatch.setattr(
        calls_repository, "patient_call_history", lambda prod_db, phone, limit: [call_row()]
    )
    headers = auth_headers(client, agent)

    found = client.get("/calls/patients/search?q=pat", headers=headers).json()
    # `params` encodes the "+" (a bare "+" in a query string means a space).
    history = client.get(
        "/calls/patients/call-history", params={"phone": PATIENT}, headers=headers
    ).json()

    assert (found["query"], found["count"]) == ("pat", 1)
    assert found["results"][0]["full_name"] == "Pat Doe"
    assert history["phone"] == PATIENT and len(history["history"]) == 1


# ---------------------------------------------------------------------
# Notes and dispositions (written to the standalone service's tables)
# ---------------------------------------------------------------------


def test_a_note_and_a_disposition_are_saved_and_audited(
    client: TestClient,
    db_session: Session,
    agent: Any,
    prod: FakeProd,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calls_repository, "get_call_by_id", lambda prod_db, call_log_id: call_row())
    headers = auth_headers(client, agent)

    note = client.post("/calls/7/notes", json={"note": "Left a voicemail"}, headers=headers)
    disposition = client.post(
        "/calls/7/disposition", json={"disposition": "voicemail"}, headers=headers
    )

    assert note.status_code == 201 and note.json() == {"id": 71}
    assert disposition.status_code == 201 and disposition.json() == {"id": 72}
    assert prod.notes[0]["call_log_id"] == 7 and prod.notes[0]["note"] == "Left a voicemail"
    assert prod.notes[0]["created_by"] == f"{agent.first_name} {agent.last_name}"
    assert {"call.note_added", "call.disposition_set"} <= set(actions(db_session))


def test_a_note_on_a_missing_call_is_a_404(
    client: TestClient, agent: Any, prod: FakeProd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calls_repository, "get_call_by_id", lambda prod_db, call_log_id: None)

    response = client.post(
        "/calls/7/notes", json={"note": "x"}, headers=auth_headers(client, agent)
    )

    assert response.status_code == 404 and prod.notes == []


# ---------------------------------------------------------------------
# Repository helpers and the live-updates manager
# ---------------------------------------------------------------------


def test_call_rows_are_read_as_utc_and_named() -> None:
    row = call_row()
    assert row["created_at"] == datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
    assert (row["display_name"], row["is_known_patient"], row["is_missed"]) == (
        "Unknown Caller",
        False,
        False,
    )

    assert call_row(patient_name="  Pat Doe ", status="Cancelled")["display_name"] == "Pat Doe"
    assert call_row(status="Cancelled")["is_missed"] is True


def test_days_resolve_the_way_the_summary_promises() -> None:
    assert resolve_days(date(2026, 10, 1), None) == (date(2026, 10, 1), date(2026, 10, 1))
    assert resolve_days(date(2026, 10, 5), date(2026, 10, 1)) == (
        date(2026, 10, 5),
        date(2026, 10, 5),
    )

    begin, end = day_range_utc(date(2026, 1, 15), date(2026, 1, 15))
    # Winter: Chicago is UTC-6.
    assert begin == datetime(2026, 1, 15, 6, 0) and end.date() == date(2026, 1, 16)


def test_the_live_updates_poll_only_reports_real_changes() -> None:
    manager = CallsConnectionManager()

    assert manager.changed((1, 5, "a")) is False  # first look sets the baseline
    assert manager.changed((1, 5, "a")) is False
    assert manager.changed((2, 6, "b")) is True
    manager.forget_baseline()
    assert manager.changed((3, 7, "c")) is False
