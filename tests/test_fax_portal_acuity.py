from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_card, make_user
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations import acuity_client
from app.models.fax_portal import (
    CCFpAcuityAppointmentType,
    CCFpAcuityAppointmentTypeCalendar,
    CCFpAcuityCalendar,
    CCFpAcuitySyncRun,
)
from app.services.fax_portal_acuity import run_acuity_sync

CALENDARS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Dallas Location",
        "location": "7701 Lemmon Avenue, Dallas, TX",
        "timezone": "America/Chicago",
    },
    {
        "id": 2,
        "name": "Fairview Location",
        "location": "Fairview, TX",
        "timezone": "America/Chicago",
    },
]

TYPES: list[dict[str, Any]] = [
    {
        "id": 10,
        "name": "Abdominal Ultrasound (Complete)",
        "category": "Abdominal",
        "duration": 30,
        "price": "124.00",
        "schedulingUrl": "https://example.as.me/schedule?appointmentType=10",
        "private": False,
        "active": True,
        "calendarIDs": [1, 2],
    },
    {
        "id": 11,
        "name": "Scrotal Ultrasound",
        "category": "Small parts",
        "duration": 30,
        "price": "124.00",
        "schedulingUrl": "https://example.as.me/schedule?appointmentType=11",
        "private": False,
        "active": True,
        "calendarIDs": [1],
    },
]


FORMS: list[dict[str, Any]] = []  # most tests do not care about intake forms


@pytest.fixture(autouse=True)
def no_real_acuity_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    # A sync that is not given forms must never reach Acuity's real API.
    monkeypatch.setattr(acuity_client, "list_forms", lambda: [])


def sync(db: Session, calendars=None, types=None, forms=None, **kwargs):
    return run_acuity_sync(
        db,
        trigger=kwargs.pop("trigger", "scheduled"),
        fetch_calendars=lambda: copy.deepcopy(CALENDARS if calendars is None else calendars),
        fetch_types=lambda: copy.deepcopy(TYPES if types is None else types),
        fetch_forms=lambda: copy.deepcopy(FORMS if forms is None else forms),
        **kwargs,
    )


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.parametrize(
    ("acuity_name", "shown"),
    [
        ("Dallas Location", "Dallas"),
        ("Fairview location", "Fairview"),
        ("Fort Worth  LOCATION ", "Fort Worth"),
        ("Dallas", "Dallas"),
        ("Location", "Location"),  # never strip down to nothing
        ("Location Annex", "Location Annex"),  # only a trailing word
    ],
)
def test_clinic_names_drop_the_trailing_word_location(acuity_name: str, shown: str) -> None:
    from app.services.fax_portal_acuity import clinic_display_name

    assert clinic_display_name(acuity_name) == shown


def test_first_run_fills_the_tables(db_session: Session) -> None:
    run = sync(db_session)

    assert run.status == "success"
    assert (run.calendars_added, run.types_added) == (2, 2)
    assert count(db_session, CCFpAcuityCalendar) == 2
    assert count(db_session, CCFpAcuityAppointmentType) == 2

    dallas = db_session.get(CCFpAcuityCalendar, 1)
    assert dallas is not None
    # Acuity says "Dallas Location"; the clinic is stored as "Dallas".
    assert (dallas.name, dallas.timezone, dallas.is_active) == (
        "Dallas",
        "America/Chicago",
        True,
    )
    assert dallas.raw["id"] == 1  # the Acuity response is kept as received
    assert dallas.raw["name"] == "Dallas Location"

    exam = db_session.get(CCFpAcuityAppointmentType, 10)
    assert exam is not None
    assert (exam.duration_min, str(exam.price), exam.category) == (30, "124.00", "Abdominal")
    assert exam.scheduling_url.endswith("appointmentType=10")

    pairs = set(
        db_session.execute(
            select(
                CCFpAcuityAppointmentTypeCalendar.acuity_appointment_type_id,
                CCFpAcuityAppointmentTypeCalendar.acuity_calendar_id,
            )
        ).all()
    )
    assert pairs == {(10, 1), (10, 2), (11, 1)}


def test_a_second_identical_run_changes_nothing(db_session: Session) -> None:
    sync(db_session)
    again = sync(db_session)

    assert again.status == "success"
    assert (
        again.calendars_added,
        again.calendars_updated,
        again.calendars_removed,
        again.types_added,
        again.types_updated,
        again.types_removed,
    ) == (0, 0, 0, 0, 0, 0)
    assert again.changes is None
    assert count(db_session, CCFpAcuityCalendar) == 2
    assert count(db_session, CCFpAcuityAppointmentTypeCalendar) == 3


def test_a_rename_updates_in_place_and_is_recorded(db_session: Session) -> None:
    sync(db_session)
    renamed = copy.deepcopy(TYPES)
    renamed[1]["name"] = "Scrotal / Testicular Ultrasound"

    run = sync(db_session, types=renamed)

    assert (run.types_added, run.types_updated, run.types_removed) == (0, 1, 0)
    assert db_session.get(CCFpAcuityAppointmentType, 11).name == "Scrotal / Testicular Ultrasound"
    assert count(db_session, CCFpAcuityAppointmentType) == 2
    assert run.changes == [
        {
            "kind": "type",
            "id": 11,
            "field": "name",
            "from": "Scrotal Ultrasound",
            "to": "Scrotal / Testicular Ultrasound",
        }
    ]


def test_ids_acuity_stops_returning_are_deactivated_never_deleted(db_session: Session) -> None:
    sync(db_session)
    # A card already points at the clinic that is about to disappear.
    card = make_card(db_session, status="ready_for_contact", acuity_calendar_id=2)

    run = sync(db_session, calendars=CALENDARS[:1], types=TYPES[:1])

    assert (run.calendars_removed, run.types_removed) == (1, 1)

    fairview = db_session.get(CCFpAcuityCalendar, 2)
    assert fairview is not None  # kept, so the card's reference stays valid
    assert fairview.is_active is False
    assert fairview.removed_at is not None
    assert db_session.get(CCFpAcuityAppointmentType, 11).is_active is False
    assert card.acuity_calendar_id == 2

    # A later run does not count the same removal again.
    assert sync(db_session, calendars=CALENDARS[:1], types=TYPES[:1]).calendars_removed == 0


def test_a_returning_id_is_reactivated(db_session: Session) -> None:
    sync(db_session)
    sync(db_session, calendars=CALENDARS[:1], types=TYPES)

    run = sync(db_session)

    assert run.calendars_updated == 1
    fairview = db_session.get(CCFpAcuityCalendar, 2)
    assert (fairview.is_active, fairview.removed_at) == (True, None)


def test_a_type_marked_inactive_by_acuity_is_deactivated(db_session: Session) -> None:
    sync(db_session)
    types = copy.deepcopy(TYPES)
    types[0]["active"] = False

    run = sync(db_session, types=types)

    assert run.types_removed == 1
    assert db_session.get(CCFpAcuityAppointmentType, 10).is_active is False


def test_which_clinics_offer_an_exam_is_rebuilt(db_session: Session) -> None:
    sync(db_session)
    types = copy.deepcopy(TYPES)
    types[0]["calendarIDs"] = [2]  # no longer offered in Dallas
    types[1]["calendarIDs"] = [1, 2, 999]  # 999 is a calendar we do not hold

    sync(db_session, types=types)

    pairs = set(
        db_session.execute(
            select(
                CCFpAcuityAppointmentTypeCalendar.acuity_appointment_type_id,
                CCFpAcuityAppointmentTypeCalendar.acuity_calendar_id,
            )
        ).all()
    )
    assert pairs == {(10, 2), (11, 1), (11, 2)}


def test_an_empty_answer_is_refused_rather_than_emptying_the_dropdowns(
    db_session: Session,
) -> None:
    sync(db_session)

    run = sync(db_session, calendars=[], types=[])

    assert run.status == "failed"
    assert "no calendars" in run.error_message
    assert db_session.get(CCFpAcuityCalendar, 1).is_active is True
    assert db_session.get(CCFpAcuityAppointmentType, 10).is_active is True


def test_an_acuity_error_is_recorded_and_changes_nothing(db_session: Session) -> None:
    sync(db_session)

    def boom() -> list[dict[str, Any]]:
        raise acuity_client.AcuityApiError("Invalid credentials")

    run = run_acuity_sync(db_session, trigger="manual", fetch_calendars=boom)

    assert run.status == "failed"
    assert run.error_message == "Invalid credentials"
    assert run.finished_at is not None
    assert count(db_session, CCFpAcuityCalendar) == 2
    assert count(db_session, CCFpAcuitySyncRun) == 2  # the success + the failure


def test_runs_record_who_and_how(db_session: Session) -> None:
    user = make_user(db_session, "front_desk")

    run = sync(db_session, trigger="manual", triggered_by_user_id=user.id)

    assert (run.trigger, run.triggered_by_user_id, run.status) == ("manual", user.id, "success")
    assert run.started_at is not None and run.finished_at is not None


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "front_desk")


def test_dropdown_endpoints_read_the_synced_tables(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    sync(db_session)
    sync(db_session, calendars=CALENDARS[:1], types=TYPES)  # Fairview removed
    headers = auth_headers(client, staff)

    calendars = client.get("/fax-portal/acuity/calendars", headers=headers).json()
    assert [c["name"] for c in calendars] == ["Dallas"]

    exams = client.get("/fax-portal/acuity/appointment-types", headers=headers).json()
    assert [e["name"] for e in exams] == ["Abdominal Ultrasound (Complete)", "Scrotal Ultrasound"]
    assert exams[0]["price"] == "124.00"
    assert all(e["available_at_calendar"] for e in exams)


def test_exams_not_offered_at_the_chosen_clinic_are_flagged(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    sync(db_session)

    exams = client.get(
        "/fax-portal/acuity/appointment-types",
        params={"calendar_id": 2},
        headers=auth_headers(client, staff),
    ).json()

    assert {e["name"]: e["available_at_calendar"] for e in exams} == {
        "Abdominal Ultrasound (Complete)": True,
        "Scrotal Ultrasound": False,  # only offered in Dallas
    }


def test_acuity_endpoints_need_access(client: TestClient, db_session: Session) -> None:
    sono = make_user(db_session, "sonographer")

    for method, path in (
        ("get", "/fax-portal/acuity/calendars"),
        ("get", "/fax-portal/acuity/appointment-types"),
        ("get", "/fax-portal/acuity/status"),
        ("post", "/fax-portal/acuity/refresh"),
    ):
        assert getattr(client, method)(path).status_code == 401, path
        assert (
            getattr(client, method)(path, headers=auth_headers(client, sono)).status_code == 403
        ), path


def test_refresh_button_runs_the_sync(
    client: TestClient,
    db_session: Session,
    staff: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acuity_client, "list_calendars", lambda: copy.deepcopy(CALENDARS))
    monkeypatch.setattr(acuity_client, "list_appointment_types", lambda: copy.deepcopy(TYPES))

    response = client.post("/fax-portal/acuity/refresh", headers=auth_headers(client, staff))

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "calendars_added": 2,
        "calendars_updated": 0,
        "calendars_removed": 0,
        "types_added": 2,
        "types_updated": 0,
        "types_removed": 0,
    }

    status = client.get("/fax-portal/acuity/status", headers=auth_headers(client, staff)).json()
    assert (status["calendars"], status["appointment_types"], status["last_status"]) == (
        2,
        2,
        "success",
    )
    assert status["last_run_at"] is not None


def test_refresh_failure_is_a_502_with_the_reason(
    client: TestClient,
    db_session: Session,
    staff: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable() -> list[dict[str, Any]]:
        raise acuity_client.AcuityApiError("Could not reach Acuity: timeout")

    monkeypatch.setattr(acuity_client, "list_calendars", unreachable)
    headers = auth_headers(client, staff)

    response = client.post("/fax-portal/acuity/refresh", headers=headers)

    assert response.status_code == 502
    assert "Could not reach Acuity" in response.json()["detail"]

    status = client.get("/fax-portal/acuity/status", headers=headers).json()
    assert status["last_status"] == "failed"
    assert "Could not reach Acuity" in status["last_error"]


def test_missing_credentials_fail_cleanly_without_leaking_anything(
    client: TestClient,
    db_session: Session,
    staff: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "acuity_user_id", None)
    monkeypatch.setattr(settings, "acuity_api_key", None)

    response = client.post("/fax-portal/acuity/refresh", headers=auth_headers(client, staff))

    assert response.status_code == 502
    assert "ACUITY_USER_ID" in response.json()["detail"]


# ---------------------------------------------------------------------
# One-time seeder
# ---------------------------------------------------------------------


def _patch_acuity(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fakes Acuity; the returned list records each call made."""
    calls: list[str] = []

    def calendars() -> list[dict[str, Any]]:
        calls.append("calendars")

        return copy.deepcopy(CALENDARS)

    def types() -> list[dict[str, Any]]:
        calls.append("types")

        return copy.deepcopy(TYPES)

    monkeypatch.setattr(acuity_client, "list_calendars", calendars)
    monkeypatch.setattr(acuity_client, "list_appointment_types", types)

    return calls


def test_the_seeder_seeds_the_first_time_and_then_does_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.fax_portal_acuity import SeedOutcome, seed_acuity_once

    calls = _patch_acuity(monkeypatch)

    outcome, run = seed_acuity_once(db_session)

    assert outcome == SeedOutcome.SEEDED
    assert (run.calendars_added, run.types_added) == (2, 2)
    assert count(db_session, CCFpAcuityCalendar) == 2
    assert calls == ["calendars", "types"]

    again, earlier = seed_acuity_once(db_session)

    assert again == SeedOutcome.ALREADY_SEEDED
    assert earlier.id == run.id
    assert calls == ["calendars", "types"]  # Acuity was not contacted again
    assert count(db_session, CCFpAcuitySyncRun) == 1


def test_a_sync_from_any_source_counts_as_seeded(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.fax_portal_acuity import SeedOutcome, seed_acuity_once

    sync(db_session)  # e.g. the hourly job got there first
    calls = _patch_acuity(monkeypatch)

    outcome, _ = seed_acuity_once(db_session)

    assert outcome == SeedOutcome.ALREADY_SEEDED
    assert calls == []


def test_a_failed_attempt_does_not_count_so_the_seeder_can_be_run_again(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.fax_portal_acuity import SeedOutcome, seed_acuity_once

    def down() -> list[dict[str, Any]]:
        raise acuity_client.AcuityApiError("Acuity is down")

    monkeypatch.setattr(acuity_client, "list_calendars", down)

    outcome, run = seed_acuity_once(db_session)

    assert outcome == SeedOutcome.FAILED
    assert run.error_message == "Acuity is down"
    assert count(db_session, CCFpAcuityCalendar) == 0

    _patch_acuity(monkeypatch)  # Acuity is back

    assert seed_acuity_once(db_session)[0] == SeedOutcome.SEEDED


def test_two_seeders_at_once_do_not_both_run(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import fax_portal_acuity
    from app.services.fax_portal_acuity import SeedOutcome, seed_acuity_once

    def locked(*args: Any, **kwargs: Any) -> Any:
        raise fax_portal_acuity.SyncAlreadyRunningError("running")

    monkeypatch.setattr(fax_portal_acuity, "run_acuity_sync", locked)

    assert seed_acuity_once(db_session) == (SeedOutcome.ALREADY_RUNNING, None)
