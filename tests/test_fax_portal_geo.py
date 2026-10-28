from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_card, make_fax, make_user
from sqlalchemy.orm import Session

from app.models.fax_portal import CCFpAcuityCalendar
from app.services import fax_portal_geo as geo

DALLAS_CLINIC = "7701 Lemmon Avenue, Ste. 260-167, Dallas, TX 75209"
FAIRVIEW_CLINIC = "100 Stacy Rd, Fairview, TX 75069"

CLINICS = [(1, "Dallas", DALLAS_CLINIC), (2, "Fairview", FAIRVIEW_CLINIC)]


# ---------------------------------------------------------------------
# ZIP codes and distances
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("500 Elm St, Dallas, TX 75201", "75201"),
        ("500 Elm St, Dallas, TX 75201-1234", "75201"),
        ("500 Elm St, Dallas TX 75201 USA", "75201"),
        ("500 Elm St, Dallas, TX,75201", "75201"),
        ("75201", "75201"),
        # A 5-digit street number is not a ZIP code.
        ("12345 Main St, Dallas, TX", None),
        ("12345 Main St, Dallas, TX 7520", None),
        # Masked, blank or missing.
        ("XXX Test St, Test TX XXXXX-XXXX", None),
        ("", None),
        (None, None),
    ],
)
def test_the_zip_code_is_read_from_the_end_of_the_address(
    address: str | None, expected: str | None
) -> None:
    assert geo.zip_of(address) == expected


def test_a_zip_code_is_located_and_unknown_ones_are_not() -> None:
    lat, lon = geo.locate(DALLAS_CLINIC) or (0.0, 0.0)

    assert 32.7 < lat < 33.0 and -97.0 < lon < -96.6
    assert geo.locate("1 Nowhere St, Dallas, TX 00000") is None
    assert geo.locate(None) is None


def test_distances_are_straight_line_miles() -> None:
    dallas = geo.locate("x, Dallas, TX 75201")
    next_door = geo.locate("x, Dallas, TX 75202")
    houston = geo.locate("x, Houston, TX 77002")

    assert dallas and next_door and houston
    assert geo.miles_between(dallas, dallas) == 0
    assert geo.miles_between(dallas, next_door) < 3
    assert 220 < geo.miles_between(dallas, houston) < 245


def test_clinics_are_ranked_nearest_first() -> None:
    downtown = geo.rank_clinics("500 Elm St, Dallas, TX 75201", CLINICS)
    mckinney = geo.rank_clinics("900 Eldorado Pkwy, McKinney, TX 75070", CLINICS)

    assert downtown and [c.name for c in downtown] == ["Dallas", "Fairview"]
    assert downtown[0].miles < downtown[1].miles
    assert mckinney and [c.name for c in mckinney] == ["Fairview", "Dallas"]


def test_no_ranking_unless_every_address_can_be_located() -> None:
    # A clinic with no ZIP code could be the nearest: guessing would be wrong.
    assert geo.rank_clinics("500 Elm St, Dallas, TX 75201", [*CLINICS, (3, "X", "No zip")]) is None
    assert geo.rank_clinics("500 Elm St, Dallas, TX 75201", [(1, "Dallas", None)]) is None
    assert geo.rank_clinics("XXX Test St, Test TX XXXXX-XXXX", CLINICS) is None
    assert geo.rank_clinics(None, CLINICS) is None
    assert geo.rank_clinics("500 Elm St, Dallas, TX 75201", []) is None


# ---------------------------------------------------------------------
# In Intake Review
# ---------------------------------------------------------------------


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "sales")


@pytest.fixture
def clinics(db_session: Session) -> None:
    for calendar_id, name, address in CLINICS:
        db_session.add(
            CCFpAcuityCalendar(acuity_calendar_id=calendar_id, name=name, location=address, raw={})
        )

    db_session.commit()


def suggestions(
    client: TestClient, db_session: Session, staff: Any, *, summary: str | None = None, **card: Any
) -> dict[str, Any]:
    fax = make_fax(db_session)
    fax.summary = summary
    row = make_card(db_session, fax, status="review_required", **card)
    db_session.commit()

    response = client.get(
        f"/fax-portal/referrals/{row.id}/intake", headers=auth_headers(client, staff)
    )

    assert response.status_code == 200, response.text

    return response.json()["suggestions"]


def test_the_nearest_clinic_is_suggested_from_the_patients_address(
    client: TestClient, db_session: Session, staff: Any, clinics: None
) -> None:
    dallas = suggestions(client, db_session, staff, address="500 Elm St, Dallas, TX 75201")
    mckinney = suggestions(
        client, db_session, staff, address="900 Eldorado Pkwy, McKinney, TX 75070"
    )

    assert dallas["clinic"]["acuity_calendar_id"] == 1
    assert dallas["clinic"]["reason"].startswith("Nearest to the patient's address")
    assert mckinney["clinic"]["acuity_calendar_id"] == 2
    assert [c["name"] for c in mckinney["clinic_distances"]] == ["Fairview", "Dallas"]


def test_a_clinic_the_fax_names_wins_over_the_nearest(
    client: TestClient, db_session: Session, staff: Any, clinics: None
) -> None:
    result = suggestions(
        client,
        db_session,
        staff,
        summary="Please schedule at the ScanX Fairview location.",
        address="500 Elm St, Dallas, TX 75201",
    )

    assert result["clinic"]["acuity_calendar_id"] == 2
    assert result["clinic"]["reason"] != "" and "Nearest" not in result["clinic"]["reason"]


def test_a_patient_far_from_every_clinic_gets_distances_but_no_suggestion(
    client: TestClient, db_session: Session, staff: Any, clinics: None
) -> None:
    result = suggestions(client, db_session, staff, address="1 Pike St, Seattle, WA 98101")

    assert result["clinic"] is None
    assert len(result["clinic_distances"]) == 2
    assert all(c["miles"] > geo.MAX_SUGGEST_MILES for c in result["clinic_distances"])


def test_without_a_usable_address_nothing_is_suggested(
    client: TestClient, db_session: Session, staff: Any, clinics: None
) -> None:
    for address in (None, "XXX Test St, Test TX XXXXX-XXXX", "12345 Main St, Dallas, TX"):
        result = suggestions(client, db_session, staff, address=address)

        assert result["clinic"] is None and result["clinic_distances"] == []


def test_distances_still_show_once_a_clinic_is_chosen(
    client: TestClient, db_session: Session, staff: Any, clinics: None
) -> None:
    result = suggestions(
        client,
        db_session,
        staff,
        address="500 Elm St, Dallas, TX 75201",
        acuity_calendar_id=2,
        acuity_calendar_name="Fairview",
    )

    # No suggestion over a choice already made, but the distances are useful.
    assert result["clinic"] is None
    assert [c["name"] for c in result["clinic_distances"]] == ["Dallas", "Fairview"]
