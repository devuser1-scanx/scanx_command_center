from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from fax_portal_helpers import (
    add_clinic_and_exam,
    auth_headers,
    make_card,
    make_fax,
    make_user,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fax_portal import CCFpCallLog, CCFpFax, CCFpReferralStatusHistory

BOARD = "/fax-portal/board"


def columns(response) -> dict[str, dict]:
    return {c["code"]: c for c in response.json()["columns"]}


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


# ---------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------


def test_board_requires_login(client: TestClient) -> None:
    assert client.get(BOARD).status_code == 401


def test_roles_outside_the_section_are_refused(client: TestClient, db_session: Session) -> None:
    sonographer = make_user(db_session, "sonographer")

    for path in (BOARD, "/fax-portal/counts", "/fax-portal/agents"):
        response = client.get(path, headers=auth_headers(client, sonographer))

        assert response.status_code == 403, path


def test_admin_and_sales_can_open_the_board(client: TestClient, db_session: Session) -> None:
    for role in ("admin", "sales"):
        user = make_user(db_session, role)

        assert client.get(BOARD, headers=auth_headers(client, user)).status_code == 200, role


# ---------------------------------------------------------------------
# Columns and cards
# ---------------------------------------------------------------------


def test_columns_follow_cc_fp_status_order_and_skip_off_board_statuses(
    client: TestClient, db_session: Session
) -> None:
    headers = auth_headers(client, make_user(db_session, "sales"))

    codes = [c["code"] for c in client.get(BOARD, headers=headers).json()["columns"]]

    assert codes == [
        "review_required",
        "ready_for_contact",
        "follow_up_due",
        "callback_requested",
        "fax_provider_name_pending",
        "didnt_respond",
        "appointment_booked",
        "not_referral",
        "exception",
    ]
    # "declined" has on_board = false, so it is never a column.
    assert "declined" not in codes


def test_cards_are_newest_first_with_the_card_fields(
    client: TestClient, db_session: Session
) -> None:
    agent = make_user(db_session, "sales", is_agent=True)
    fax = make_fax(db_session)
    older = make_card(
        db_session,
        fax,
        first_name="Older",
        status="ready_for_contact",
        status_changed_at=ago(hours=5),
    )
    newer = make_card(
        db_session,
        fax,
        first_name="Newer",
        last_name="Person",
        status="ready_for_contact",
        status_changed_at=ago(minutes=5),
        assigned_user_id=agent.id,
        is_urgent=True,
        first_viewed_at=ago(minutes=1),
        acuity_calendar_name="Dallas Location",
        physician_name="Dr. Who",
    )
    add_clinic_and_exam(db_session, newer)
    db_session.commit()

    response = client.get(BOARD, headers=auth_headers(client, agent))
    cards = columns(response)["ready_for_contact"]["cards"]

    assert [c["id"] for c in cards] == [newer.id, older.id]

    top = cards[0]
    assert top["display_name"] == "Newer Person"
    assert top["is_new"] is False
    assert top["is_urgent"] is True
    assert top["physician_name"] == "Dr. Who"
    assert top["exam_1"].startswith("Exam ")
    assert top["assignee"]["user_id"] == agent.id
    assert top["assignee"]["initials"] == (agent.first_name[0] + agent.last_name[0]).upper()
    assert top["row_version"] == 1

    # Never opened: shows the NEW tag.
    assert cards[1]["is_new"] is True
    assert cards[1]["assignee"] is None
    assert cards[1]["exam_1"] is None


def test_card_without_a_patient_is_named_unknown(client: TestClient, db_session: Session) -> None:
    make_card(db_session, first_name=None, last_name=None, status="review_required")
    db_session.commit()

    cards = columns(
        client.get(BOARD, headers=auth_headers(client, make_user(db_session, "admin")))
    )["review_required"]["cards"]

    assert cards[0]["display_name"] == "Unknown patient"


def test_attempt_dots_come_from_the_call_log(client: TestClient, db_session: Session) -> None:
    card = make_card(db_session, status="follow_up_due")
    for counts in (True, False, True):
        db_session.add(
            CCFpCallLog(referral_id=card.id, to_number="+12145550100", counts_toward_limit=counts)
        )
    db_session.commit()

    cards = columns(
        client.get(BOARD, headers=auth_headers(client, make_user(db_session, "admin")))
    )["follow_up_due"]["cards"]

    assert cards[0]["attempts"] == ["miss", "other", "miss"]


def test_due_time_only_for_open_cards_being_worked(client: TestClient, db_session: Session) -> None:
    soon = ago(hours=-3)
    overdue = ago(hours=2)
    make_card(db_session, status="callback_requested", callback_at=soon, next_action_at=overdue)
    make_card(db_session, status="appointment_booked", next_action_at=overdue)
    db_session.commit()

    result = columns(
        client.get(BOARD, headers=auth_headers(client, make_user(db_session, "admin")))
    )

    assert result["callback_requested"]["cards"][0]["due_at"] is not None
    assert result["appointment_booked"]["cards"][0]["due_at"] is None


# ---------------------------------------------------------------------
# Hiding old cards in finished columns (server-side, nothing deleted)
# ---------------------------------------------------------------------


def test_old_cards_in_finished_columns_are_hidden_but_kept(
    client: TestClient, db_session: Session
) -> None:
    # Seeded: Not Referrals 7 days, Appointment Booked 14, Didn't Respond 30.
    fresh = make_card(db_session, status="not_referral", status_changed_at=ago(days=6))
    stale = make_card(db_session, status="not_referral", status_changed_at=ago(days=8))
    booked_stale = make_card(
        db_session, status="appointment_booked", status_changed_at=ago(days=15)
    )
    booked_ok = make_card(db_session, status="appointment_booked", status_changed_at=ago(days=13))
    quiet_ok = make_card(db_session, status="didnt_respond", status_changed_at=ago(days=29))
    quiet_stale = make_card(db_session, status="didnt_respond", status_changed_at=ago(days=31))
    # Active columns never hide, however old.
    working = make_card(db_session, status="follow_up_due", status_changed_at=ago(days=200))
    db_session.commit()

    headers = auth_headers(client, make_user(db_session, "admin"))
    result = columns(client.get(BOARD, headers=headers))

    assert [c["id"] for c in result["not_referral"]["cards"]] == [fresh.id]
    assert result["not_referral"]["hidden_count"] == 1
    assert result["not_referral"]["visible_count"] == 1
    assert [c["id"] for c in result["appointment_booked"]["cards"]] == [booked_ok.id]
    assert [c["id"] for c in result["didnt_respond"]["cards"]] == [quiet_ok.id]
    assert [c["id"] for c in result["follow_up_due"]["cards"]] == [working.id]
    assert result["follow_up_due"]["hidden_count"] == 0

    # "Show older" brings them back for the chosen column only.
    older = columns(client.get(BOARD, params={"show_older": "not_referral"}, headers=headers))

    assert {c["id"] for c in older["not_referral"]["cards"]} == {fresh.id, stale.id}
    assert older["not_referral"]["showing_older"] is True
    assert older["not_referral"]["hidden_count"] == 0
    assert [c["id"] for c in older["appointment_booked"]["cards"]] == [booked_ok.id]
    assert booked_stale.id not in {c["id"] for c in older["appointment_booked"]["cards"]}
    assert quiet_stale.id not in {c["id"] for c in older["didnt_respond"]["cards"]}


# ---------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------


def test_assignee_filters(client: TestClient, db_session: Session) -> None:
    me = make_user(db_session, "sales", is_agent=True)
    other = make_user(db_session, "sales", is_agent=True)
    mine = make_card(db_session, status="ready_for_contact", assigned_user_id=me.id)
    theirs = make_card(db_session, status="ready_for_contact", assigned_user_id=other.id)
    nobody = make_card(db_session, status="ready_for_contact")
    db_session.commit()

    headers = auth_headers(client, me)

    def ids(**params: str) -> set[int]:
        response = client.get(BOARD, params=params, headers=headers)

        return {c["id"] for c in columns(response)["ready_for_contact"]["cards"]}

    assert ids() == {mine.id, theirs.id, nobody.id}
    assert ids(assignee="all") == {mine.id, theirs.id, nobody.id}
    assert ids(assignee="me") == {mine.id}
    assert ids(assignee="unassigned") == {nobody.id}


def test_search_by_name_and_phone(client: TestClient, db_session: Session) -> None:
    maria = make_card(
        db_session,
        status="ready_for_contact",
        first_name="Maria",
        last_name="Delgado",
        phone="(214) 555-0187",
        phone_e164="+12145550187",
    )
    make_card(
        db_session,
        status="ready_for_contact",
        first_name="Kevin",
        last_name="Liu",
        phone_e164="+19725550162",
    )
    db_session.commit()

    headers = auth_headers(client, make_user(db_session, "sales"))

    def ids(q: str) -> set[int]:
        response = client.get(BOARD, params={"q": q}, headers=headers)

        return {c["id"] for c in columns(response)["ready_for_contact"]["cards"]}

    assert ids("delgado") == {maria.id}
    assert ids("MARIA DEL") == {maria.id}
    assert ids("555-0187") == {maria.id}
    assert ids("(214) 555-0187") == {maria.id}
    assert ids("zzz") == set()


def test_filters_change_the_column_counts(client: TestClient, db_session: Session) -> None:
    me = make_user(db_session, "sales", is_agent=True)
    make_card(db_session, status="ready_for_contact", assigned_user_id=me.id)
    make_card(db_session, status="ready_for_contact")
    db_session.commit()

    result = columns(client.get(BOARD, params={"assignee": "me"}, headers=auth_headers(client, me)))

    assert result["ready_for_contact"]["visible_count"] == 1


# ---------------------------------------------------------------------
# Summary pills and sidebar counts
# ---------------------------------------------------------------------


def test_summary_pills(client: TestClient, db_session: Session) -> None:
    make_card(db_session, status="review_required")
    make_card(db_session, status="review_required")
    make_card(db_session, status="follow_up_due", next_action_at=ago(hours=1))
    make_card(db_session, status="follow_up_due", next_action_at=ago(hours=-5))
    make_card(db_session, status="appointment_booked", booked_at=datetime.now(UTC))
    make_card(db_session, status="appointment_booked", booked_at=ago(days=3))
    make_card(db_session, status="fax_provider_name_pending")
    db_session.commit()

    summary = client.get(
        BOARD, headers=auth_headers(client, make_user(db_session, "admin"))
    ).json()["summary"]

    assert summary["review"] == 2
    assert summary["overdue"] == 1
    assert summary["booked_today"] == 1
    assert summary["fax_provider"] == 1
    assert summary["received_today"] == 7


def test_sidebar_counts(client: TestClient, db_session: Session) -> None:
    me = make_user(db_session, "sales", is_agent=True)
    other = make_user(db_session, "sales", is_agent=True)
    make_card(db_session, status="review_required")
    make_card(db_session, status="review_required")
    # Mine and due.
    make_card(
        db_session, status="follow_up_due", assigned_user_id=me.id, next_action_at=ago(hours=1)
    )
    make_card(
        db_session, status="callback_requested", assigned_user_id=me.id, callback_at=ago(minutes=1)
    )
    # Mine but not yet due, someone else's, closed.
    make_card(
        db_session, status="follow_up_due", assigned_user_id=me.id, next_action_at=ago(hours=-2)
    )
    make_card(
        db_session, status="follow_up_due", assigned_user_id=other.id, next_action_at=ago(hours=1)
    )
    make_card(
        db_session, status="appointment_booked", assigned_user_id=me.id, next_action_at=ago(hours=1)
    )
    db_session.commit()

    response = client.get("/fax-portal/counts", headers=auth_headers(client, me))

    assert response.json() == {"call_queue": 2, "intake_review": 2}


def test_agents_list_is_every_active_sales_user(client: TestClient, db_session: Session) -> None:
    paused = make_user(db_session, "sales", is_agent=True, accepting_new=False)
    unconfigured = make_user(db_session, "sales")  # no settings row needed
    make_user(db_session, "sonographer")
    admin = make_user(db_session, "admin")

    response = client.get("/fax-portal/agents", headers=auth_headers(client, admin))

    assert {(a["user_id"], a["accepting_new"]) for a in response.json()} == {
        (paused.id, False),
        (unconfigured.id, True),
    }


# ---------------------------------------------------------------------
# Cheap polling with ?since=
# ---------------------------------------------------------------------


def test_since_returns_nothing_until_a_card_changes(
    client: TestClient, db_session: Session
) -> None:
    card = make_card(db_session, status="ready_for_contact")
    db_session.commit()
    headers = auth_headers(client, make_user(db_session, "sales"))

    first = client.get(BOARD, headers=headers).json()
    assert first["changed"] is True

    # Nothing happened since the last response (which is well past the overlap).
    later = datetime.fromisoformat(first["server_time"]) + timedelta(seconds=60)
    quiet = client.get(BOARD, params={"since": later.isoformat()}, headers=headers).json()
    assert quiet["changed"] is False
    assert quiet["columns"] == []
    assert quiet["summary"] is None

    # A move bumps updated_at, so the next poll gets the full board.
    resp = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json={"to_status": "follow_up_due", "row_version": 1},
        headers=headers,
    )
    assert resp.status_code == 200

    changed = client.get(
        BOARD,
        params={"since": (datetime.fromisoformat(first["server_time"])).isoformat()},
        headers=headers,
    ).json()
    assert changed["changed"] is True
    assert changed["columns"]


def test_since_can_be_sent_as_a_header_so_every_poll_has_the_same_url(
    client: TestClient, db_session: Session
) -> None:
    make_card(db_session, status="ready_for_contact")
    db_session.commit()
    headers = auth_headers(client, make_user(db_session, "sales"))

    first = client.get(BOARD, headers=headers).json()
    later = (datetime.fromisoformat(first["server_time"]) + timedelta(seconds=60)).isoformat()

    quiet = client.get(BOARD, headers={**headers, "X-Board-Since": later}).json()
    changed = client.get(
        BOARD,
        headers={**headers, "X-Board-Since": (ago(days=1)).isoformat()},
    ).json()

    assert quiet["changed"] is False
    assert changed["changed"] is True


# ---------------------------------------------------------------------
# Move and assign endpoints
# ---------------------------------------------------------------------


def test_move_endpoint_updates_card_and_returns_new_version(
    client: TestClient, db_session: Session
) -> None:
    card = make_card(db_session, status="ready_for_contact")
    db_session.commit()
    headers = auth_headers(client, make_user(db_session, "sales"))

    response = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json={"to_status": "follow_up_due", "row_version": 1, "source": "drag"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": card.id,
        "from_status": "ready_for_contact",
        "to_status": "follow_up_due",
        "row_version": 2,
    }
    db_session.refresh(card)
    assert card.status == "follow_up_due"

    history = db_session.scalars(
        select(CCFpReferralStatusHistory).where(CCFpReferralStatusHistory.referral_id == card.id)
    ).one()
    assert history.source == "drag"


def test_invalid_move_is_422_with_a_readable_reason_and_changes_nothing(
    client: TestClient, db_session: Session
) -> None:
    card = make_card(db_session, status="review_required")
    db_session.commit()

    response = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json={"to_status": "ready_for_contact", "row_version": 1},
        headers=auth_headers(client, make_user(db_session, "sales")),
    )

    assert response.status_code == 422
    assert "clinic and Exam 1" in response.json()["detail"]
    db_session.refresh(card)
    assert (card.status, card.row_version) == ("review_required", 1)


def test_stale_drag_is_409(client: TestClient, db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact", row_version=4)
    db_session.commit()

    response = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json={"to_status": "follow_up_due", "row_version": 3},
        headers=auth_headers(client, make_user(db_session, "sales")),
    )

    assert response.status_code == 409
    assert "changed by someone else" in response.json()["detail"]
    db_session.refresh(card)
    assert card.status == "ready_for_contact"


def test_move_unknown_referral_is_404(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/fax-portal/referrals/999999999/move",
        json={"to_status": "follow_up_due", "row_version": 1},
        headers=auth_headers(client, make_user(db_session, "sales")),
    )

    assert response.status_code == 404


def test_move_requires_access(client: TestClient, db_session: Session) -> None:
    card = make_card(db_session, status="ready_for_contact")
    db_session.commit()
    body = {"to_status": "follow_up_due", "row_version": 1}

    assert client.post(f"/fax-portal/referrals/{card.id}/move", json=body).status_code == 401
    assert (
        client.post(
            f"/fax-portal/referrals/{card.id}/move",
            json=body,
            headers=auth_headers(client, make_user(db_session, "sonographer")),
        ).status_code
        == 403
    )


def test_manager_reopen_over_http(client: TestClient, db_session: Session) -> None:
    card = make_card(db_session, status="didnt_respond")
    db_session.commit()
    body = {"to_status": "follow_up_due", "row_version": 1, "note": "Patient called back"}

    staff = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json=body,
        headers=auth_headers(client, make_user(db_session, "sales")),
    )
    admin = client.post(
        f"/fax-portal/referrals/{card.id}/move",
        json=body,
        headers=auth_headers(client, make_user(db_session, "admin")),
    )

    assert staff.status_code == 403
    assert admin.status_code == 200


def test_assign_endpoint_is_admin_only(client: TestClient, db_session: Session) -> None:
    agent = make_user(db_session, "sales", is_agent=True)
    card = make_card(db_session, status="ready_for_contact")
    db_session.commit()
    body = {"assigned_user_id": agent.id, "row_version": 1}

    refused = client.post(
        f"/fax-portal/referrals/{card.id}/assign",
        json=body,
        headers=auth_headers(client, make_user(db_session, "sales")),
    )
    done = client.post(
        f"/fax-portal/referrals/{card.id}/assign",
        json=body,
        headers=auth_headers(client, make_user(db_session, "admin")),
    )

    assert refused.status_code == 403
    assert done.status_code == 200
    assert done.json() == {"id": card.id, "assigned_user_id": agent.id, "row_version": 2}


def test_ingested_fax_shows_up_on_the_board(client: TestClient, db_session: Session) -> None:
    """End to end with the webhook ingest: a fax creates a card in Review
    Required, assigned and marked NEW."""
    from app.services.fax_portal_ingest import process_payload, store_payload

    agent = make_user(db_session, "sales", is_agent=True)
    body = b'{"Job ID": "board-e2e", "PHI Name": ["Ann Alpha"], "PHI Phone": ["(214) 555-0111"]}'
    process_payload(db_session, store_payload(db_session, body).id)

    result = columns(client.get(BOARD, headers=auth_headers(client, agent)))
    cards = result["review_required"]["cards"]

    assert len(cards) == 1
    assert cards[0]["display_name"] == "Ann Alpha"
    assert cards[0]["is_new"] is True
    assert cards[0]["assignee"]["user_id"] == agent.id
    assert db_session.scalar(select(CCFpFax.id).where(CCFpFax.job_id == "board-e2e")) is not None
