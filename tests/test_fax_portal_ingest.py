from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.auth import CCRole, CCUser, CCUserRole
from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpAgent,
    CCFpComprehendPayload,
    CCFpFax,
    CCFpFaxDocument,
    CCFpFaxInsurance,
    CCFpFaxPatient,
    CCFpFaxProvider,
    CCFpProvider,
    CCFpReferralBoard,
    CCFpReferralStatusHistory,
)
from app.services import fax_portal_ingest
from app.services.fax_portal_assignment import assign_new_referral
from app.services.fax_portal_ingest import process_payload, store_payload

FIXTURE = Path(__file__).parent / "fixtures" / "comprehend_sample.json"


def sample_body(**overrides: Any) -> bytes:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(overrides)

    return json.dumps(payload).encode()


def ingest(db: Session, body: bytes) -> fax_portal_ingest.IngestResult:
    return process_payload(db, store_payload(db, body).id)


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


_user_seq = 0


def make_agent(
    db: Session,
    *,
    accepting_new: bool = True,
    max_open: int | None = None,
    last_assigned_at: datetime | None = None,
    is_active: bool = True,
    role: str = "sales",
) -> CCUser:
    global _user_seq
    _user_seq += 1

    user = CCUser(
        email=f"fp-agent-{_user_seq}@example.com",
        first_name="Agent",
        last_name=str(_user_seq),
        password_hash=hash_password("Agent@12345678"),
        is_active=is_active,
        must_change_password=False,
        failed_login_attempts=0,
    )
    db.add(user)
    db.flush()

    # Role membership is what makes someone assignable.
    db.add(
        CCUserRole(
            user_id=user.id,
            role_id=db.scalar(select(CCRole.id).where(CCRole.code == role)),
        )
    )

    db.add(
        CCFpAgent(
            user_id=user.id,
            accepting_new=accepting_new,
            max_open=max_open,
            last_assigned_at=last_assigned_at,
        )
    )
    db.flush()

    return user


def two_patient_body(job_id: str = "multi-1") -> bytes:
    return json.dumps(
        {
            "Job ID": job_id,
            "Date": "2026-10-12T21:44:06+00:00",
            "Term number": "(469) 429-7432",
            "Multi patient": "1",
            "Patient count": "2",
            "PHI Name": ["Ann Alpha", "Bob Beta"],
            "PHI Phone": ["(214) 555-0111", "(214) 555-0122"],
            "PHI DOB": ["1980-01-02", "1975-03-04"],
            "Name": ["ScanX Health", "Dr Refer"],
            "type (roles)": [[], ["Referring"]],
            "FAX": ["(469) 429-7432", "(972) 555-0100"],
            "NPI": [None, "1111111111"],
            "patient Index": [None, None],
        }
    ).encode()


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_sample_fax_creates_everything(db_session: Session) -> None:
    agent = make_agent(db_session)

    result = ingest(db_session, sample_body())

    assert result.created is True
    assert result.parse_error is None
    assert len(result.referral_ids) == 1

    fax = db_session.scalar(select(CCFpFax).where(CCFpFax.id == result.fax_id))
    assert fax is not None
    assert fax.job_id == "7765d711-7c00-4b92-8c72-5884fc356bd9"
    assert fax.page_count == 5
    assert fax.received_at == datetime(2026, 10, 12, 21, 44, 6, tzinfo=UTC)

    assert count(db_session, CCFpFaxPatient) == 1
    assert count(db_session, CCFpFaxProvider) == 11
    assert count(db_session, CCFpFaxDocument) == 4
    assert count(db_session, CCFpFaxInsurance) == 1

    card = db_session.get(CCFpReferralBoard, result.referral_ids[0])
    assert card is not None
    assert card.status == "review_required"
    assert card.first_viewed_at is None  # shows the NEW tag
    assert (card.first_name, card.last_name) == ("John", "Doe")
    assert card.gender == "Male"
    assert card.physician_name == "Doctor Order, MD"
    assert card.physician_fax_no == "214-555-0102"
    assert card.fax_patient_id is not None
    assert card.assigned_user_id == agent.id
    assert card.assignment_source == "auto"
    assert card.row_version == 1

    history = db_session.scalars(
        select(CCFpReferralStatusHistory).where(CCFpReferralStatusHistory.referral_id == card.id)
    ).all()
    assert [(h.from_status, h.to_status, h.source) for h in history] == [
        (None, "review_required", "ingest")
    ]

    payload_row = db_session.get(CCFpComprehendPayload, result.payload_id)
    assert payload_row is not None
    assert payload_row.fax_id == fax.id
    assert payload_row.parsed_at is not None
    assert payload_row.parse_error is None


def test_referring_provider_is_deduped_into_directory(db_session: Session) -> None:
    ingest(db_session, sample_body())

    provider = db_session.scalar(select(CCFpProvider).where(CCFpProvider.npi == "1234567890"))

    assert provider is not None
    assert provider.name == "Doctor Order, MD"
    assert provider.organization == "Example Urology Clinic"
    assert provider.fax_e164 == "+12145550102"

    linked = db_session.scalars(
        select(CCFpFaxProvider).where(CCFpFaxProvider.provider_id == provider.id)
    ).all()
    assert [row.name for row in linked] == ["Doctor Order, MD"]


def test_same_npi_on_two_faxes_reuses_one_provider(db_session: Session) -> None:
    ingest(db_session, sample_body())
    ingest(db_session, sample_body(**{"Job ID": "another-job"}))

    assert count(db_session, CCFpFax) == 2
    assert (
        db_session.scalar(
            select(func.count()).select_from(CCFpProvider).where(CCFpProvider.npi == "1234567890")
        )
        == 1
    )


def test_activity_log_has_no_phi(db_session: Session) -> None:
    ingest(db_session, sample_body())

    entries = db_session.scalars(select(CCFpActivityLog).order_by(CCFpActivityLog.id)).all()

    assert [e.action for e in entries] == ["referral_created", "fax_ingested"]
    assert all(e.section == "system" and e.user_id is None for e in entries)

    blob = json.dumps([e.details for e in entries])
    assert "Doe" not in blob
    assert "John" not in blob


# ---------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------


def test_replaying_the_same_delivery_creates_nothing_new(db_session: Session) -> None:
    first = ingest(db_session, sample_body())
    second = ingest(db_session, sample_body())

    assert second.created is False
    assert second.fax_id == first.fax_id
    assert second.referral_ids == first.referral_ids

    assert count(db_session, CCFpFax) == 1
    assert count(db_session, CCFpReferralBoard) == 1
    assert count(db_session, CCFpFaxPatient) == 1
    assert count(db_session, CCFpFaxProvider) == 11
    # Every delivery is kept, retries included.
    assert count(db_session, CCFpComprehendPayload) == 2

    retry = db_session.get(CCFpComprehendPayload, second.payload_id)
    assert retry is not None
    assert retry.fax_id == first.fax_id
    assert retry.parsed_at is not None


def test_same_job_id_with_changed_body_still_does_not_duplicate(db_session: Session) -> None:
    ingest(db_session, sample_body())
    again = ingest(db_session, sample_body(**{"PHI Summary of Docs": "edited"}))

    assert again.created is False
    assert count(db_session, CCFpFax) == 1
    assert count(db_session, CCFpReferralBoard) == 1


# ---------------------------------------------------------------------
# Multi-patient and no-patient faxes
# ---------------------------------------------------------------------


def test_multi_patient_fax_gets_one_card_per_patient(db_session: Session) -> None:
    make_agent(db_session)
    make_agent(db_session)

    result = ingest(db_session, two_patient_body())

    assert len(result.referral_ids) == 2

    cards = [db_session.get(CCFpReferralBoard, rid) for rid in result.referral_ids]
    assert [(c.first_name, c.last_name) for c in cards if c] == [("Ann", "Alpha"), ("Bob", "Beta")]
    assert all(c and c.status == "review_required" for c in cards)
    assert len({c.fax_patient_id for c in cards if c}) == 2
    # Both cards share the fax's referrer, but the load is spread over agents.
    assert len({c.provider_id for c in cards if c}) == 1
    assert len({c.assigned_user_id for c in cards if c}) == 2

    assert count(db_session, CCFpFax) == 1


def test_fax_without_patient_still_gets_one_card(db_session: Session) -> None:
    result = ingest(db_session, json.dumps({"Job ID": "nopatient", "Result": "Success"}).encode())

    assert result.created is True
    assert len(result.referral_ids) == 1

    card = db_session.get(CCFpReferralBoard, result.referral_ids[0])
    assert card is not None
    assert card.fax_patient_id is None
    assert card.first_name is None
    assert card.status == "review_required"
    # No agents exist in this test: the card is simply unassigned.
    assert card.assigned_user_id is None


def test_possible_duplicate_is_flagged_but_not_blocked(db_session: Session) -> None:
    first = ingest(db_session, two_patient_body("dup-1"))
    second = ingest(db_session, two_patient_body("dup-2"))

    assert len(second.referral_ids) == 2

    new_cards = [db_session.get(CCFpReferralBoard, rid) for rid in second.referral_ids]

    assert [c.duplicate_of_referral_id for c in new_cards if c] == first.referral_ids
    # The first fax's cards are not duplicates of anything.
    assert all(
        (c := db_session.get(CCFpReferralBoard, rid)) and c.duplicate_of_referral_id is None
        for rid in first.referral_ids
    )


def test_overlong_values_are_truncated_not_fatal(db_session: Session) -> None:
    result = ingest(
        db_session,
        json.dumps(
            {"Job ID": "long", "PHI Name": ["A " + "B" * 300], "PHI Gender": ["Unspecified-ish"]}
        ).encode(),
    )

    assert result.parse_error is None

    card = db_session.get(CCFpReferralBoard, result.referral_ids[0])
    assert card is not None
    assert card.last_name is not None
    assert len(card.last_name) == 100
    assert card.gender == "Unspecifie"  # VARCHAR(10)


# ---------------------------------------------------------------------
# Failure handling: nothing is silently lost
# ---------------------------------------------------------------------


def test_non_json_body_is_stored_and_gets_a_fallback_card(db_session: Session) -> None:
    result = ingest(db_session, b"this is not json")

    assert result.created is True
    assert result.parse_error == "Request body is not a JSON object."
    assert len(result.referral_ids) == 1

    payload_row = db_session.get(CCFpComprehendPayload, result.payload_id)
    assert payload_row is not None
    assert payload_row.payload == {"_unparsed_body": "this is not json"}
    assert payload_row.parsed_at is None
    assert payload_row.fax_id == result.fax_id

    card = db_session.get(CCFpReferralBoard, result.referral_ids[0])
    assert card is not None
    assert card.status == "review_required"


def test_json_array_body_is_not_a_payload(db_session: Session) -> None:
    result = ingest(db_session, b'[{"Job ID": "x"}]')

    assert result.parse_error == "Request body is not a JSON object."
    assert len(result.referral_ids) == 1


def test_retry_of_an_unparseable_body_does_not_pile_up_cards(db_session: Session) -> None:
    ingest(db_session, b"garbage")
    ingest(db_session, b"garbage")

    assert count(db_session, CCFpFax) == 1
    assert count(db_session, CCFpReferralBoard) == 1
    assert count(db_session, CCFpComprehendPayload) == 2


def test_parse_failure_keeps_error_and_creates_fallback_card(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: dict[str, Any]) -> None:
        raise RuntimeError("mapping bug")

    monkeypatch.setattr(fax_portal_ingest, "parse_payload", boom)

    result = ingest(db_session, sample_body())

    assert result.created is True
    assert result.parse_error is not None
    assert result.parse_error.startswith("RuntimeError")
    assert len(result.referral_ids) == 1

    payload_row = db_session.get(CCFpComprehendPayload, result.payload_id)
    assert payload_row is not None
    assert payload_row.parsed_at is None
    assert payload_row.parse_error == result.parse_error
    # The raw payload is intact, so it can be re-parsed after a fix.
    assert payload_row.payload["Job ID"] == "7765d711-7c00-4b92-8c72-5884fc356bd9"

    # No half-written extraction rows survive the failed attempt.
    assert count(db_session, CCFpFaxPatient) == 0
    assert count(db_session, CCFpFaxProvider) == 0

    fax = db_session.get(CCFpFax, result.fax_id)
    assert fax is not None
    assert fax.job_id == "7765d711-7c00-4b92-8c72-5884fc356bd9"


def test_missing_job_id_still_dedupes_identical_redeliveries(db_session: Session) -> None:
    body = json.dumps({"PHI Name": ["Ann Alpha"]}).encode()

    first = ingest(db_session, body)
    second = ingest(db_session, body)

    assert first.created is True
    assert second.created is False
    assert count(db_session, CCFpFax) == 1

    fax = db_session.get(CCFpFax, first.fax_id)
    assert fax is not None
    assert fax.job_id.startswith("missing-")


def test_nul_characters_do_not_break_jsonb(db_session: Session) -> None:
    result = ingest(
        db_session, json.dumps({"Job ID": "nul", "PHI Name": ["Ann\u0000 Alpha"]}).encode()
    )

    assert result.parse_error is None

    card = db_session.get(CCFpReferralBoard, result.referral_ids[0])
    assert card is not None
    assert card.last_name == "Alpha"


# ---------------------------------------------------------------------
# Agent assignment (docs/implementation-plan.md section 2)
# ---------------------------------------------------------------------


def new_card(db: Session, fax_id: int, **kwargs: Any) -> CCFpReferralBoard:
    card = CCFpReferralBoard(fax_id=fax_id, **kwargs)
    db.add(card)
    db.flush()

    return card


@pytest.fixture
def fax_id(db_session: Session) -> int:
    fax = CCFpFax(job_id="assign-fax", received_at=datetime.now(UTC))
    db_session.add(fax)
    db_session.flush()

    return fax.id


def test_assigns_agent_with_fewest_open_cards(db_session: Session, fax_id: int) -> None:
    busy, idle = make_agent(db_session), make_agent(db_session)
    new_card(db_session, fax_id, assigned_user_id=busy.id, status="ready_for_contact")
    new_card(db_session, fax_id, assigned_user_id=busy.id, status="follow_up_due")

    card = new_card(db_session, fax_id)

    assert assign_new_referral(db_session, card) == idle.id
    assert card.assignment_source == "auto"
    assert card.assigned_at is not None


def test_closed_cards_do_not_count_as_load(db_session: Session, fax_id: int) -> None:
    done, other = make_agent(db_session), make_agent(db_session)
    # `done` has many finished cards (is_open = false) and one open one.
    for status in ("appointment_booked", "didnt_respond", "not_referral", "declined"):
        new_card(db_session, fax_id, assigned_user_id=done.id, status=status)
    new_card(db_session, fax_id, assigned_user_id=other.id, status="ready_for_contact")
    new_card(db_session, fax_id, assigned_user_id=other.id, status="ready_for_contact")

    card = new_card(db_session, fax_id)

    assert assign_new_referral(db_session, card) == done.id


def test_tie_goes_to_oldest_last_assigned(db_session: Session, fax_id: int) -> None:
    now = datetime.now(UTC)
    recent = make_agent(db_session, last_assigned_at=now - timedelta(minutes=5))
    oldest = make_agent(db_session, last_assigned_at=now - timedelta(days=2))
    make_agent(db_session, last_assigned_at=now - timedelta(hours=1))

    card = new_card(db_session, fax_id)

    assert assign_new_referral(db_session, card) == oldest.id
    assert recent.id != oldest.id


def test_never_assigned_agent_wins_the_tie(db_session: Session, fax_id: int) -> None:
    make_agent(db_session, last_assigned_at=datetime.now(UTC) - timedelta(days=30))
    fresh = make_agent(db_session, last_assigned_at=None)

    assert assign_new_referral(db_session, new_card(db_session, fax_id)) == fresh.id


def test_round_robin_when_loads_are_equal(db_session: Session, fax_id: int) -> None:
    a, b = make_agent(db_session), make_agent(db_session)

    picks = []
    for _ in range(4):
        card = new_card(db_session, fax_id, status="ready_for_contact")
        picks.append(assign_new_referral(db_session, card))

    assert picks[0] != picks[1]
    assert picks[0] == picks[2]
    assert picks[1] == picks[3]
    assert {picks[0], picks[1]} == {a.id, b.id}


def test_agent_at_max_open_is_skipped(db_session: Session, fax_id: int) -> None:
    capped = make_agent(db_session, max_open=1)
    free = make_agent(db_session)
    new_card(db_session, fax_id, assigned_user_id=capped.id, status="ready_for_contact")
    # Give `free` more open cards so only the cap keeps `capped` out.
    new_card(db_session, fax_id, assigned_user_id=free.id, status="ready_for_contact")
    new_card(db_session, fax_id, assigned_user_id=free.id, status="ready_for_contact")

    assert assign_new_referral(db_session, new_card(db_session, fax_id)) == free.id


def test_not_accepting_or_inactive_agents_are_skipped(db_session: Session, fax_id: int) -> None:
    make_agent(db_session, accepting_new=False)
    make_agent(db_session, is_active=False)
    eligible = make_agent(db_session)

    assert assign_new_referral(db_session, new_card(db_session, fax_id)) == eligible.id


def test_no_eligible_agent_leaves_card_unassigned(db_session: Session, fax_id: int) -> None:
    make_agent(db_session, accepting_new=False)
    make_agent(db_session, max_open=1)
    # Fill the capped agent.
    capped = db_session.scalars(select(CCFpAgent).where(CCFpAgent.max_open == 1)).one()
    new_card(db_session, fax_id, assigned_user_id=capped.user_id, status="ready_for_contact")

    card = new_card(db_session, fax_id)

    assert assign_new_referral(db_session, card) is None
    assert card.assigned_user_id is None
    assert card.assignment_source is None


def test_assignment_bumps_last_assigned_at(db_session: Session, fax_id: int) -> None:
    agent = make_agent(db_session)

    assign_new_referral(db_session, new_card(db_session, fax_id))
    db_session.refresh(agent)
    agent_row = db_session.get(CCFpAgent, agent.id)

    assert agent_row is not None
    assert agent_row.last_assigned_at is not None


# ---------------------------------------------------------------------
# Who is assignable: role membership, not a hand-kept list
# ---------------------------------------------------------------------


def make_role_user(db: Session, role: str, *, is_active: bool = True) -> CCUser:
    """A user with a role but NO cc_fp_agent row."""
    user = make_agent(db, role=role, is_active=is_active)
    db.query(CCFpAgent).filter(CCFpAgent.user_id == user.id).delete()
    db.flush()

    return user


@pytest.mark.parametrize("role", ["sales", "front_desk"])
def test_sales_and_front_desk_users_are_assignable_without_an_agent_row(
    db_session: Session, fax_id: int, role: str
) -> None:
    user = make_role_user(db_session, role)
    assert db_session.get(CCFpAgent, user.id) is None

    card = new_card(db_session, fax_id)

    assert assign_new_referral(db_session, card) == user.id
    # The settings row is created on first need, so it can be locked and tracked.
    row = db_session.get(CCFpAgent, user.id)
    assert row is not None
    assert row.accepting_new is True
    assert row.last_assigned_at is not None


@pytest.mark.parametrize("role", ["admin", "sonographer"])
def test_other_roles_are_never_assigned(db_session: Session, fax_id: int, role: str) -> None:
    make_role_user(db_session, role)

    card = new_card(db_session, fax_id)

    assert assign_new_referral(db_session, card) is None
    assert card.assigned_user_id is None


def test_a_stale_agent_row_without_the_role_is_ignored(db_session: Session, fax_id: int) -> None:
    # Has a cc_fp_agent row (e.g. from before) but no longer holds the role.
    stale = make_agent(db_session, role="sonographer")
    holder = make_role_user(db_session, "front_desk")

    assert db_session.get(CCFpAgent, stale.id) is not None
    assert assign_new_referral(db_session, new_card(db_session, fax_id)) == holder.id


def test_inactive_role_holders_are_skipped(db_session: Session, fax_id: int) -> None:
    make_role_user(db_session, "sales", is_active=False)
    active = make_role_user(db_session, "front_desk")

    assert assign_new_referral(db_session, new_card(db_session, fax_id)) == active.id


def test_role_holders_share_load_without_any_setup(db_session: Session, fax_id: int) -> None:
    a = make_role_user(db_session, "sales")
    b = make_role_user(db_session, "front_desk")

    picks = [
        assign_new_referral(db_session, new_card(db_session, fax_id, status="ready_for_contact"))
        for _ in range(4)
    ]

    assert sorted(picks) == sorted([a.id, a.id, b.id, b.id])
    assert picks[0] != picks[1]


def test_pausing_through_the_settings_row_still_works(db_session: Session, fax_id: int) -> None:
    paused = make_role_user(db_session, "sales")
    working = make_role_user(db_session, "front_desk")
    db_session.add(CCFpAgent(user_id=paused.id, accepting_new=False))
    db_session.flush()

    for _ in range(3):
        card = new_card(db_session, fax_id, status="ready_for_contact")

        assert assign_new_referral(db_session, card) == working.id
