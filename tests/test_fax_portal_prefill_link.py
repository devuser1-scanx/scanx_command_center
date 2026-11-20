from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import add_clinic_and_exam, auth_headers, make_card, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.twilio_client import TwilioSendResult
from app.models.fax_portal import CCFpActivityLog, CCFpAcuityAppointmentType, CCFpMessageLog
from app.services import fax_portal_booking, fax_portal_case, fax_portal_messages
from app.services import fax_portal_prefill_link as prefill

PHONE = "+12145550187"
BASE = "https://api.example.test"
SCHEDULING_URL = "https://app.acuityscheduling.com/schedule.php?owner=1&appointmentType=9"


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "sales")


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real time must not decide whether texts can go out, and the booking link
    # never asks the real Acuity API for its forms.
    monkeypatch.setattr(fax_portal_case, "text_window_open", lambda now=None: True)
    monkeypatch.setattr(fax_portal_booking, "list_forms", lambda: [])
    monkeypatch.setattr(fax_portal_booking, "_forms_cache", None)
    monkeypatch.setattr(settings, "public_api_base_url", BASE)
    monkeypatch.setattr(settings, "twilio_prefill_link_content_sid", None)


class FakeTwilio:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, *, to: str, body: str, status_callback: str | None = None
    ) -> TwilioSendResult:
        self.calls.append({"to": to, "body": body})

        return TwilioSendResult(message_sid=f"SM{len(self.calls):032d}", from_number="+14695550100")


@pytest.fixture
def twilio(monkeypatch: pytest.MonkeyPatch) -> FakeTwilio:
    fake = FakeTwilio()
    monkeypatch.setattr(fax_portal_messages, "send_sms", fake)

    return fake


class FakeContent:
    """Stands in for Twilio's Content Template send (the RCS card)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> TwilioSendResult:
        self.calls.append(kwargs)

        return TwilioSendResult(message_sid="SM" + "1" * 32, from_number="rcs:scanx_agent")


@pytest.fixture
def content(monkeypatch: pytest.MonkeyPatch) -> FakeContent:
    fake = FakeContent()
    monkeypatch.setattr(fax_portal_messages, "send_content_message", fake)
    monkeypatch.setattr(settings, "twilio_prefill_link_content_sid", "HX" + "a" * 32)

    return fake


def ready_card(db: Session, **kwargs: Any):
    kwargs.setdefault("status", "ready_for_contact")
    kwargs.setdefault("first_name", "Maria")
    kwargs.setdefault("last_name", "Lopez")
    kwargs.setdefault("email", "maria@example.com")
    kwargs.setdefault("phone", "(214) 555-0187")
    kwargs.setdefault("phone_e164", PHONE)
    card = make_card(db, **kwargs)
    add_clinic_and_exam(db, card)
    db.execute(CCFpAcuityAppointmentType.__table__.update().values(scheduling_url=SCHEDULING_URL))
    db.commit()

    return card


def draft(client: TestClient, user: Any, card: Any) -> dict[str, Any]:
    response = client.get(
        f"/fax-portal/referrals/{card.id}/prefill-link", headers=auth_headers(client, user)
    )
    assert response.status_code == 200, response.text

    return response.json()


def send(client: TestClient, user: Any, card: Any):
    return client.post(
        f"/fax-portal/referrals/{card.id}/prefill-link", headers=auth_headers(client, user)
    )


# ---------------------------------------------------------------------
# What the agent sees and what is sent
# ---------------------------------------------------------------------


def test_the_draft_shows_the_text_without_a_real_link(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = ready_card(db_session, physician_name="Dr. Whitaker")

    result = draft(client, staff, card)

    assert result["blocked_reason"] is None
    assert "Hi Maria," in result["message"] and "Dr. Whitaker" in result["message"]
    assert "[personal link]" in result["message"] and BASE not in result["message"]


def test_sending_texts_the_patient_a_link_that_holds_no_personal_details(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    card = ready_card(db_session)

    response = send(client, staff, card)

    assert response.status_code == 200, response.text
    [call] = twilio.calls
    assert call["to"] == PHONE
    assert f"{BASE}/fax-portal/book/" in call["body"] and "STOP" not in call["body"]
    # The text carries a token, never the details Acuity will be prefilled with.
    for private in ("Lopez", "maria@example.com", "2145550187"):
        assert private not in call["body"].split("book/")[1]

    logged = db_session.scalars(select(CCFpMessageLog)).all()
    assert len(logged) == 1 and logged[0].referral_id == card.id
    actions = db_session.scalars(select(CCFpActivityLog.action)).all()
    assert "send_prefill_link" in actions


@pytest.mark.parametrize(
    ("setting", "reason"),
    [("review", "Intake Review"), ("closed", "closed")],
)
def test_it_cannot_be_sent_for_cards_in_review_or_closed(
    client: TestClient,
    db_session: Session,
    staff: Any,
    twilio: FakeTwilio,
    setting: str,
    reason: str,
) -> None:
    card = ready_card(
        db_session, status="review_required" if setting == "review" else "appointment_booked"
    )

    assert reason in (draft(client, staff, card)["blocked_reason"] or "")
    assert send(client, staff, card).status_code == 422
    assert twilio.calls == []


def test_it_needs_the_public_address_an_exam_and_a_deliverable_number(
    client: TestClient,
    db_session: Session,
    staff: Any,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = ready_card(db_session)

    monkeypatch.setattr(settings, "public_api_base_url", None)
    assert "PUBLIC_API_BASE_URL" in draft(client, staff, card)["blocked_reason"]
    assert send(client, staff, card).status_code == 422

    monkeypatch.setattr(settings, "public_api_base_url", BASE)
    no_phone = ready_card(db_session, phone_e164=None, phone=None)
    assert "phone number" in draft(client, staff, no_phone)["blocked_reason"]
    assert twilio.calls == []


def test_only_portal_staff_can_send_it(
    client: TestClient, db_session: Session, twilio: FakeTwilio
) -> None:
    card = ready_card(db_session)
    outsider = make_user(db_session, "sonographer")

    assert send(client, outsider, card).status_code == 403
    assert twilio.calls == []


# ---------------------------------------------------------------------
# What the patient's tap does
# ---------------------------------------------------------------------


def open_link(client: TestClient, token: str):
    return client.get(f"/fax-portal/book/{token}", follow_redirects=False)


def test_the_link_redirects_to_acuity_with_the_details_filled_in(
    client: TestClient, db_session: Session
) -> None:
    card = ready_card(db_session)

    response = open_link(client, prefill.make_token(card.id))

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        "https://app.acuityscheduling.com/schedule.php"
    )
    query = parse_qs(target.query)
    assert query["firstName"] == ["Maria"] and query["lastName"] == ["Lopez"]
    assert query["email"] == ["maria@example.com"] and query["appointmentType"] == ["9"]
    assert query["calendarID"] == [str(card.acuity_calendar_id)]

    opened = db_session.scalars(
        select(CCFpActivityLog).where(CCFpActivityLog.action == "prefill_link_opened")
    ).one()
    assert opened.referral_id == card.id and opened.user_id is None


def test_the_link_shows_on_the_case_timeline_once_opened(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = ready_card(db_session)
    open_link(client, prefill.make_token(card.id))

    timeline = client.get(
        f"/fax-portal/referrals/{card.id}/case", headers=auth_headers(client, staff)
    ).json()["timeline"]

    assert any(event["title"] == "Patient opened the booking link" for event in timeline)


@pytest.mark.parametrize(
    "bad",
    ["not-a-token", jwt.encode({"r": 1}, "x" * 40, algorithm="HS256")],
)
def test_a_made_up_or_forged_link_is_refused(client: TestClient, bad: str) -> None:
    response = open_link(client, bad)

    assert response.status_code == 410 and "not valid" in response.text


def test_an_expired_link_is_refused(client: TestClient, db_session: Session) -> None:
    card = ready_card(db_session)
    old = prefill.make_token(card.id, now=datetime.now(UTC) - timedelta(days=8))

    response = open_link(client, old)

    assert response.status_code == 410 and "expired" in response.text


def test_a_login_token_cannot_be_used_as_a_booking_link(
    client: TestClient, db_session: Session
) -> None:
    card = ready_card(db_session)
    other = jwt.encode(
        {"r": card.id, "exp": datetime.now(UTC) + timedelta(days=1), "typ": "access"},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    assert open_link(client, other).status_code == 410


@pytest.mark.parametrize("status", ["appointment_booked", "didnt_respond", "not_referral"])
def test_the_link_stops_working_once_the_referral_is_closed(
    client: TestClient, db_session: Session, status: str
) -> None:
    card = ready_card(db_session, status=status)

    response = open_link(client, prefill.make_token(card.id))

    assert response.status_code == 410 and "already complete" in response.text


def test_the_plain_text_says_when_the_link_expires(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = ready_card(db_session)

    message = draft(client, staff, card)["message"]

    assert "expires in 7 days" in message and "Please don't share" in message


def test_with_a_card_template_the_link_goes_as_an_rcs_card(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio, content: FakeContent
) -> None:
    card = ready_card(db_session)

    preview = draft(client, staff, card)
    assert preview["as_card"] is True
    assert "[Book Appointment]" in preview["message"] and "expires in 7 days" in preview["message"]

    response = send(client, staff, card)

    assert response.status_code == 200, response.text
    assert response.json()["channel"] == "rcs"
    assert twilio.calls == []
    [call] = content.calls
    assert call["to"] == PHONE and call["content_sid"] == "HX" + "a" * 32
    variables = call["content_variables"]
    assert variables["1"] == "Maria" and variables["2"]
    assert variables["3"].startswith(f"{BASE}/fax-portal/book/")
    # The log holds the plain-text version.
    logged = db_session.scalars(select(CCFpMessageLog)).one()
    assert "expires in 7 days" in logged.body
