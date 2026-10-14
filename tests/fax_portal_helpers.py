from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from conftest import create_test_user
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpAcuityAppointmentType,
    CCFpAcuityCalendar,
    CCFpAgent,
    CCFpFax,
    CCFpReferralBoard,
    CCFpReferralExam,
)

PASSWORD = "Helper@12345678"

_seq = 0


def next_seq() -> int:
    global _seq
    _seq += 1

    return _seq


def make_user(db: Session, role_code: str, *, is_agent: bool = False, **agent: Any) -> CCUser:
    n = next_seq()
    user = create_test_user(
        db,
        email=f"fp-{role_code}-{n}@example.com",
        password=PASSWORD,
        role_code=role_code,
        first_name=f"First{n}",
        last_name=f"Last{n}",
    )

    if is_agent:
        db.add(CCFpAgent(user_id=user.id, **agent))

    db.commit()

    return user


def auth_headers(client: TestClient, user: CCUser) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})

    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_fax(db: Session) -> CCFpFax:
    fax = CCFpFax(job_id=f"job-{next_seq()}", received_at=datetime.now(UTC))
    db.add(fax)
    db.flush()

    return fax


def make_card(db: Session, fax: CCFpFax | None = None, **kwargs: Any) -> CCFpReferralBoard:
    kwargs.setdefault("first_name", "Pat")
    kwargs.setdefault("last_name", f"Test{next_seq()}")

    card = CCFpReferralBoard(fax_id=(fax or make_fax(db)).id, **kwargs)
    db.add(card)
    db.flush()

    return card


def add_clinic_and_exam(db: Session, card: CCFpReferralBoard) -> None:
    """Gives a card the clinic + Exam 1 that releasing to Sales requires."""
    n = next_seq()
    # Acuity ids are supplied by Acuity; any unique number works here.
    calendar_id, type_id = 9_000_000 + n, 8_000_000 + n

    db.add(
        CCFpAcuityCalendar(
            acuity_calendar_id=calendar_id, name=f"Clinic {n}", raw={"id": calendar_id}
        )
    )
    db.add(
        CCFpAcuityAppointmentType(
            acuity_appointment_type_id=type_id, name=f"Exam {n}", raw={"id": type_id}
        )
    )
    db.flush()

    card.acuity_calendar_id = calendar_id
    card.acuity_calendar_name = f"Clinic {n}"
    db.add(
        CCFpReferralExam(
            referral_id=card.id,
            slot=1,
            acuity_appointment_type_id=type_id,
            appointment_type=f"Exam {n}",
        )
    )
    db.flush()
