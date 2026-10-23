from __future__ import annotations

import base64
import copy
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import add_clinic_and_exam, auth_headers, make_card, make_user
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.prod_session import get_prod_db
from app.integrations import twilio_client
from app.integrations.twilio_client import TwilioApiError, TwilioSendResult
from app.main import app
from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpAcuityAppointmentType,
    CCFpContactOptout,
    CCFpMessageLog,
    CCFpReferralBoard,
)
from app.schemas.fax_portal import BookingMatch
from app.services import fax_portal_booking, fax_portal_case, fax_portal_messages

PHONE = "+12145550187"
WORK_HOURS = lambda now=None: True  # noqa: E731
QUIET_HOURS = lambda now=None: False  # noqa: E731


@pytest.fixture
def staff(db_session: Session) -> Any:
    return make_user(db_session, "front_desk")


@pytest.fixture(autouse=True)
def inside_sending_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real time must not decide whether these tests can send.
    monkeypatch.setattr(fax_portal_case, "text_window_open", WORK_HOURS)


@pytest.fixture(autouse=True)
def no_acuity_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    # The booking link asks Acuity for its intake forms: never the real API.
    monkeypatch.setattr(fax_portal_booking, "list_forms", lambda: [])
    monkeypatch.setattr(fax_portal_booking, "_forms_cache", None)


class FakeTwilio:
    """Stands in for Twilio's send_sms and records every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.from_number: str | None = "+14695550100"
        self.error: Exception | None = None

    def __call__(
        self, *, to: str, body: str, status_callback: str | None = None
    ) -> TwilioSendResult:
        self.calls.append({"to": to, "body": body, "status_callback": status_callback})

        if self.error is not None:
            raise self.error

        return TwilioSendResult(
            message_sid=f"SM{len(self.calls):032d}", from_number=self.from_number
        )


@pytest.fixture
def twilio(monkeypatch: pytest.MonkeyPatch) -> FakeTwilio:
    fake = FakeTwilio()
    monkeypatch.setattr(fax_portal_messages, "send_sms", fake)

    return fake


def card_for_texting(db: Session, **kwargs: Any) -> CCFpReferralBoard:
    kwargs.setdefault("status", "follow_up_due")
    kwargs.setdefault("first_name", "Maria")
    kwargs.setdefault("phone", "(214) 555-0187")
    kwargs.setdefault("phone_e164", PHONE)
    card = make_card(db, **kwargs)
    db.commit()

    return card


def send(
    client: TestClient,
    headers: dict[str, str],
    card_id: int,
    body: str = "Hello Maria",
    **extra: Any,
):
    return client.post(
        f"/fax-portal/referrals/{card_id}/messages", json={"body": body, **extra}, headers=headers
    )


def rows(db: Session) -> list[CCFpMessageLog]:
    return list(db.scalars(select(CCFpMessageLog).order_by(CCFpMessageLog.id)))


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------


def test_a_text_is_sent_and_recorded(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    card = card_for_texting(db_session)

    response = send(
        client, auth_headers(client, staff), card.id, "Hi Maria, book here", template_code=None
    )

    assert response.status_code == 201, response.text
    assert response.json() == {
        "message_id": rows(db_session)[0].id,
        "status": "sent",
        "channel": "sms",
    }
    assert twilio.calls[0]["to"] == PHONE and twilio.calls[0]["body"] == "Hi Maria, book here"

    message = rows(db_session)[0]
    assert (message.referral_id, message.direction, message.status, message.user_id) == (
        card.id,
        "outbound",
        "sent",
        staff.id,
    )
    assert message.twilio_message_sid.startswith("SM") and message.from_number == "+14695550100"
    assert message.to_number == PHONE
    db_session.refresh(card)
    assert card.last_contact_at is not None

    entry = db_session.scalars(
        select(CCFpActivityLog).where(CCFpActivityLog.action == "message_send")
    ).one()
    assert "Maria" not in str(entry.details)  # no message text in the audit log


def test_an_rcs_sender_is_recorded_as_rcs(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    twilio.from_number = "rcs:scanx_agent"
    card = card_for_texting(db_session)

    response = send(client, auth_headers(client, staff), card.id)

    assert response.json()["channel"] == "rcs"
    assert rows(db_session)[0].channel == "rcs"


def test_the_status_callback_url_is_sent_to_twilio_when_configured(
    client: TestClient,
    db_session: Session,
    staff: Any,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "public_api_base_url", None)  # whatever the environment has
    card = card_for_texting(db_session)
    headers = auth_headers(client, staff)

    send(client, headers, card.id)
    assert twilio.calls[-1]["status_callback"] is None  # not configured

    monkeypatch.setattr(settings, "public_api_base_url", "https://api.example.com/")
    send(client, headers, card.id)

    assert twilio.calls[-1]["status_callback"] == (
        "https://api.example.com/fax-portal/webhooks/twilio/message-status"
    )


@pytest.mark.parametrize(
    ("status", "reason_part"),
    [
        ("review_required", "Release"),
        ("appointment_booked", "closed"),
        ("declined", "closed"),
    ],
)
def test_texts_are_blocked_for_unreleased_and_closed_referrals(
    client: TestClient,
    db_session: Session,
    staff: Any,
    twilio: FakeTwilio,
    status: str,
    reason_part: str,
) -> None:
    card = card_for_texting(db_session, status=status)

    response = send(client, auth_headers(client, staff), card.id)

    assert response.status_code == 422 and reason_part in response.json()["detail"]
    assert twilio.calls == [] and rows(db_session) == []


def test_texts_are_blocked_without_a_valid_number(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    card = card_for_texting(db_session, phone=None, phone_e164=None)

    response = send(client, auth_headers(client, staff), card.id)

    assert response.status_code == 422 and "phone number" in response.json()["detail"]


def test_do_not_call_and_opt_out_block_texts(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    dnc = card_for_texting(db_session, phone_e164="+12145550001")
    stopped = card_for_texting(db_session, phone_e164="+12145550002")
    db_session.add(CCFpContactOptout(phone_e164="+12145550001", dnc_at=datetime.now(UTC)))
    db_session.add(CCFpContactOptout(phone_e164="+12145550002", sms_opt_out_at=datetime.now(UTC)))
    db_session.commit()
    headers = auth_headers(client, staff)

    first, second = send(client, headers, dnc.id), send(client, headers, stopped.id)

    assert first.status_code == 422 and "do-not-call" in first.json()["detail"]
    assert second.status_code == 422 and "opted out" in second.json()["detail"]
    assert twilio.calls == []


def test_one_stop_blocks_every_referral_with_that_number(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    one = card_for_texting(db_session)
    two = card_for_texting(db_session)  # same number, another referral
    db_session.add(CCFpContactOptout(phone_e164=PHONE, sms_opt_out_at=datetime.now(UTC)))
    db_session.commit()
    headers = auth_headers(client, staff)

    assert send(client, headers, one.id).status_code == 422
    assert send(client, headers, two.id).status_code == 422


def test_quiet_hours_block_texts(
    client: TestClient,
    db_session: Session,
    staff: Any,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fax_portal_case, "text_window_open", QUIET_HOURS)
    card = card_for_texting(db_session)
    headers = auth_headers(client, staff)

    response = send(client, headers, card.id)

    assert response.status_code == 422 and "AM" in response.json()["detail"]
    detail = client.get(f"/fax-portal/referrals/{card.id}/case", headers=headers).json()
    assert detail["can_text"] is False and "9 PM" in detail["text_blocked_reason"]
    assert twilio.calls == []


def test_unfilled_placeholders_and_unknown_templates_are_refused(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    card = card_for_texting(db_session)
    headers = auth_headers(client, staff)

    unfilled = send(client, headers, card.id, "Book here: {booking_link}")
    unknown = send(client, headers, card.id, "Hello", template_code="nope")
    empty = client.post(
        f"/fax-portal/referrals/{card.id}/messages", json={"body": ""}, headers=headers
    )

    assert unfilled.status_code == 422 and "placeholders" in unfilled.json()["detail"]
    assert unknown.status_code == 422
    assert empty.status_code == 422
    assert twilio.calls == []


# ---------------------------------------------------------------------
# When Twilio refuses
# ---------------------------------------------------------------------


def test_a_twilio_failure_is_kept_on_the_message_and_reported(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    twilio.error = TwilioApiError("Invalid 'To' phone number", code=21211)
    card = card_for_texting(db_session)

    response = send(client, auth_headers(client, staff), card.id)

    assert response.status_code == 502 and "Invalid 'To'" in response.json()["detail"]
    message = rows(db_session)[0]  # the attempt is on record
    assert (message.status, message.error_code) == ("failed", "21211")
    assert db_session.get(CCFpContactOptout, PHONE) is None


def test_a_stop_error_from_twilio_records_the_opt_out(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    twilio.error = TwilioApiError("Attempt to send to unsubscribed recipient", code=21610)
    card = card_for_texting(db_session)
    other = card_for_texting(db_session)
    headers = auth_headers(client, staff)

    response = send(client, headers, card.id)

    assert response.status_code == 422 and "opted out" in response.json()["detail"]
    optout = db_session.get(CCFpContactOptout, PHONE)
    assert optout.sms_opt_out_at is not None and optout.source == "stop_reply"
    # From now on every referral with this number is blocked before reaching Twilio.
    calls_before = len(twilio.calls)
    assert send(client, headers, other.id).status_code == 422
    assert len(twilio.calls) == calls_before


def test_unconfigured_twilio_fails_cleanly(
    client: TestClient, db_session: Session, staff: Any, twilio: FakeTwilio
) -> None:
    twilio.error = RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured")
    card = card_for_texting(db_session)

    response = send(client, auth_headers(client, staff), card.id)

    assert response.status_code == 502 and "not configured" in response.json()["detail"]
    assert "TWILIO" not in response.text


# ---------------------------------------------------------------------
# Twilio's delivery updates
# ---------------------------------------------------------------------

CALLBACK_URL = "https://api.example.com/fax-portal/webhooks/twilio/message-status"


def sign(params: dict[str, str], token: str, url: str = CALLBACK_URL) -> str:
    signed = url + "".join(f"{key}{params[key]}" for key in sorted(params))

    return base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()


@pytest.fixture
def signed_callbacks(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "twilio_auth_token", "unit-test-auth-token")
    monkeypatch.setattr(settings, "public_api_base_url", "https://api.example.com")

    return "unit-test-auth-token"


def post_status(
    client: TestClient, token: str, params: dict[str, str], *, signature: str | None = None
):
    return client.post(
        "/fax-portal/webhooks/twilio/message-status",
        data=params,
        headers={"X-Twilio-Signature": signature if signature is not None else sign(params, token)},
    )


def sent_message(db: Session, sid: str = "SMabc") -> CCFpMessageLog:
    card = card_for_texting(db)
    message = CCFpMessageLog(
        referral_id=card.id,
        to_number=PHONE,
        body="hi",
        status="sent",
        twilio_message_sid=sid,
        channel="sms",
    )
    db.add(message)
    db.commit()

    return message


def test_delivery_updates_move_a_message_forward(
    client: TestClient, db_session: Session, signed_callbacks: str
) -> None:
    message = sent_message(db_session)

    response = post_status(
        client, signed_callbacks, {"MessageSid": "SMabc", "MessageStatus": "delivered"}
    )

    assert response.status_code == 204
    db_session.refresh(message)
    assert message.status == "delivered"

    post_status(client, signed_callbacks, {"MessageSid": "SMabc", "MessageStatus": "read"})
    db_session.refresh(message)
    assert message.status == "read"


def test_updates_arriving_late_never_move_a_message_backwards(
    client: TestClient, db_session: Session, signed_callbacks: str
) -> None:
    message = sent_message(db_session)

    post_status(client, signed_callbacks, {"MessageSid": "SMabc", "MessageStatus": "delivered"})
    post_status(client, signed_callbacks, {"MessageSid": "SMabc", "MessageStatus": "sent"})

    db_session.refresh(message)
    assert message.status == "delivered"


def test_a_failed_update_records_the_error_and_a_stop_error_opts_the_number_out(
    client: TestClient, db_session: Session, signed_callbacks: str
) -> None:
    message = sent_message(db_session)

    post_status(
        client,
        signed_callbacks,
        {"MessageSid": "SMabc", "MessageStatus": "undelivered", "ErrorCode": "21610"},
    )

    db_session.refresh(message)
    assert (message.status, message.error_code) == ("undelivered", "21610")
    assert db_session.get(CCFpContactOptout, PHONE).sms_opt_out_at is not None


def test_an_rcs_sender_in_the_update_corrects_the_channel(
    client: TestClient, db_session: Session, signed_callbacks: str
) -> None:
    message = sent_message(db_session)

    post_status(
        client,
        signed_callbacks,
        {"MessageSid": "SMabc", "MessageStatus": "delivered", "From": "rcs:scanx"},
    )

    db_session.refresh(message)
    assert message.channel == "rcs"


def test_an_update_for_an_unknown_message_is_acknowledged_and_ignored(
    client: TestClient, db_session: Session, signed_callbacks: str
) -> None:
    response = post_status(
        client, signed_callbacks, {"MessageSid": "SMunknown", "MessageStatus": "delivered"}
    )

    assert response.status_code == 204


def test_updates_with_a_bad_or_missing_signature_are_rejected(
    client: TestClient, db_session: Session, signed_callbacks: str
) -> None:
    message = sent_message(db_session)
    params = {"MessageSid": "SMabc", "MessageStatus": "delivered"}

    wrong = post_status(client, signed_callbacks, params, signature="not-a-signature")
    other_token = post_status(
        client, signed_callbacks, params, signature=sign(params, "other-token")
    )
    missing = client.post("/fax-portal/webhooks/twilio/message-status", data=params)
    # A signature made for different parameters (tampered body).
    tampered = post_status(
        client,
        signed_callbacks,
        {**params, "MessageStatus": "read"},
        signature=sign(params, signed_callbacks),
    )

    assert [r.status_code for r in (wrong, other_token, missing, tampered)] == [403, 403, 403, 403]
    db_session.refresh(message)
    assert message.status == "sent"


def test_without_an_auth_token_every_update_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", None)
    params = {"MessageSid": "SMabc", "MessageStatus": "delivered"}

    response = client.post(
        "/fax-portal/webhooks/twilio/message-status",
        data=params,
        headers={"X-Twilio-Signature": sign(params, "")},
    )

    assert response.status_code == 403


def test_the_signature_check_matches_twilios_published_algorithm() -> None:
    # Twilio's published request-validation test vector (twilio-python's RequestValidator tests).
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675309",
        "Digits": "1234",
        "From": "+14158675309",
        "To": "+18005551212",
    }
    token = "12345"
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    expected = "RSOYDt4T1cUTdK1PDd93/VVr8B8="

    assert sign(params, token, url) == expected

    original = settings.twilio_auth_token
    settings.twilio_auth_token = token
    try:
        assert twilio_client.validate_twilio_signature(url=url, params=params, signature=expected)
        assert not twilio_client.validate_twilio_signature(url=url, params=params, signature="x")
        assert not twilio_client.validate_twilio_signature(url=url, params=params, signature=None)
    finally:
        settings.twilio_auth_token = original


# ---------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------


def test_templates_are_filled_from_the_referral(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = card_for_texting(db_session, physician_name="Dr. Whitaker", acuity_calendar_id=None)
    add_clinic_and_exam(db_session, card)
    exam_name = db_session.scalar(
        select(CCFpAcuityAppointmentType.name).order_by(
            CCFpAcuityAppointmentType.acuity_appointment_type_id.desc()
        )
    )
    db_session.execute(
        CCFpAcuityAppointmentType.__table__.update().values(
            scheduling_url="https://app.acuityscheduling.com/schedule.php?owner=1&appointmentType=9"
        )
    )
    db_session.commit()

    templates = client.get(
        f"/fax-portal/referrals/{card.id}/message-templates", headers=auth_headers(client, staff)
    ).json()
    by_code = {t["code"]: t for t in templates}

    assert set(by_code) == {
        "booking_link_en",
        "booking_link_es",
        "missed_call_en",
        "callback_confirmation_en",
        "follow_up_day_3_en",
        "follow_up_day_6_en",
        "follow_up_day_9_en",
    }
    english = by_code["booking_link_en"]
    assert english["missing"] == []
    assert "Maria" in english["body"] and "Dr. Whitaker" in english["body"]
    assert exam_name.lower() in english["body"]
    assert (
        "https://app.acuityscheduling.com/schedule.php?owner=1&appointmentType=9" in english["body"]
    )
    assert "calendarID=" in english["body"] and "firstName=Maria" in english["body"]
    # Every first-contact template carries the opt-out line. The follow-up texts
    # (days 3, 6, 9) are the supplied wording, which does not include it.
    follow_ups = {code for code in by_code if code.startswith("follow_up_")}
    assert all("STOP" in t["body"] for t in templates if t["code"] not in follow_ups)

    # The follow-ups are filled in with the patient's first name and need no link.
    for code in follow_ups:
        assert by_code[code]["missing"] == [] and "Maria" in by_code[code]["body"]
        assert "{" not in by_code[code]["body"]
    assert "(469) 804-6999" in by_code["follow_up_day_3_en"]["body"]
    assert "Reply YES" in by_code["follow_up_day_6_en"]["body"]
    assert "final follow-up" in by_code["follow_up_day_9_en"]["body"]


def test_details_that_are_not_known_stay_as_placeholders(
    client: TestClient, db_session: Session, staff: Any
) -> None:
    card = card_for_texting(db_session, first_name=None, physician_name=None)

    english = {
        t["code"]: t
        for t in client.get(
            f"/fax-portal/referrals/{card.id}/message-templates",
            headers=auth_headers(client, staff),
        ).json()
    }["booking_link_en"]

    assert set(english["missing"]) == {"patient_first", "provider", "exam", "booking_link"}
    assert "{booking_link}" in english["body"]


# ---------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------


def test_the_booking_link_keeps_acuitys_own_parameters_and_adds_the_patient() -> None:
    card = CCFpReferralBoard(
        first_name="Maria",
        last_name="O'Brien",
        phone="(214) 555-0187",
        email="maria+test@example.com",
        acuity_calendar_id=12853377,
    )

    url = fax_portal_booking.prefill_booking_url(
        "https://app.acuityscheduling.com/schedule.php?owner=1&appointmentType=9", card
    )

    assert url.startswith(
        "https://app.acuityscheduling.com/schedule.php?owner=1&appointmentType=9&"
    )
    assert "calendarID=12853377" in url
    assert "lastName=O%27Brien" in url
    assert "email=maria%2Btest%40example.com" in url
    assert "phone=%28214%29+555-0187" in url


def test_the_booking_link_skips_what_is_missing_and_never_overrides_acuitys_values() -> None:
    card = CCFpReferralBoard(first_name="Maria", last_name=None, phone=None, email=None)

    url = fax_portal_booking.prefill_booking_url(
        "https://example.as.me/schedule?calendarID=5&owner=1", card
    )

    assert url == "https://example.as.me/schedule?calendarID=5&owner=1&firstName=Maria"


@pytest.fixture
def prod_override() -> Any:
    app.dependency_overrides[get_prod_db] = lambda: None
    yield
    app.dependency_overrides.pop(get_prod_db, None)


def test_booking_info_has_the_link_and_the_appointment_matches(
    client: TestClient,
    db_session: Session,
    staff: Any,
    prod_override: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = card_for_texting(db_session)
    add_clinic_and_exam(db_session, card)
    db_session.execute(
        CCFpAcuityAppointmentType.__table__.update().values(
            scheduling_url="https://app.acuityscheduling.com/schedule.php?owner=1"
        )
    )
    db_session.commit()
    match = BookingMatch(
        appointment_id="APPT-9",
        first_name="Maria",
        last_name="Delgado",
        appointment_type="Abdominal Ultrasound",
        date=datetime(2026, 10, 20, 15, 30),
        time="3:30pm",
        canceled=False,
        already_linked=False,
    )
    monkeypatch.setattr(
        fax_portal_booking, "find_appointment_matches", lambda prod_db, db, c: [match]
    )

    body = client.get(
        f"/fax-portal/referrals/{card.id}/booking", headers=auth_headers(client, staff)
    ).json()

    assert body["booking_link"].startswith("https://app.acuityscheduling.com/schedule.php?owner=1&")
    assert body["booking_link_blocked_reason"] is None
    assert [m["appointment_id"] for m in body["matches"]] == ["APPT-9"]
    assert body["matches_unavailable"] is None


def test_booking_info_explains_why_there_is_no_link_yet(
    client: TestClient,
    db_session: Session,
    staff: Any,
    prod_override: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = card_for_texting(db_session)  # no exam chosen
    monkeypatch.setattr(fax_portal_booking, "find_appointment_matches", lambda *a: [])

    body = client.get(
        f"/fax-portal/referrals/{card.id}/booking", headers=auth_headers(client, staff)
    ).json()

    assert body["booking_link"] is None
    assert "Exam 1" in body["booking_link_blocked_reason"]


def test_a_failing_appointment_lookup_does_not_break_the_page(
    client: TestClient,
    db_session: Session,
    staff: Any,
    prod_override: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = card_for_texting(db_session)

    def broken(*args: Any) -> list[BookingMatch]:
        raise RuntimeError("production database is down")

    monkeypatch.setattr(fax_portal_booking, "find_appointment_matches", broken)

    response = client.get(
        f"/fax-portal/referrals/{card.id}/booking", headers=auth_headers(client, staff)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matches"] == [] and "not available" in body["matches_unavailable"]
    assert "down" not in response.text  # the reason is not leaked


def test_booking_for_a_missing_referral_is_404(
    client: TestClient, db_session: Session, staff: Any, prod_override: Any
) -> None:
    response = client.get(
        "/fax-portal/referrals/99999999/booking", headers=auth_headers(client, staff)
    )

    assert response.status_code == 404


def test_templates_are_not_mutated_between_requests() -> None:
    before = copy.deepcopy(fax_portal_messages.TEMPLATES)

    assert before == fax_portal_messages.TEMPLATES


# ---------------------------------------------------------------------
# The appointment lookup, against real Postgres
# ---------------------------------------------------------------------


@pytest.fixture
def appointment_table(db_session: Session) -> Any:
    """The production `appointment` table, created inside this test's own
    transaction (rolled back afterwards) so the real SQL can be exercised."""
    from app.models.production import Appointment

    table = Appointment.__table__
    bind = db_session.connection()
    existed = db_session.scalar(select(func.to_regclass("public.appointment")))

    if existed is None:
        table.create(bind=bind)

    return Appointment


def test_appointment_matches_find_the_patients_bookings_by_phone_or_name(
    db_session: Session, appointment_table: Any
) -> None:
    Appointment = appointment_table
    db_session.execute(Appointment.__table__.delete())
    card = make_card(
        db_session,
        first_name="Maria",
        last_name="Delgado",
        phone="(214) 555-0187",
        phone_e164=PHONE,
        status="follow_up_due",
    )
    other_card = make_card(db_session, status="ready_for_contact", acuity_appointment_id="LINKED-1")

    def appt(i: int, appointment_id: str, **kw: Any) -> Any:
        return Appointment(
            id=i,
            appointment_id=appointment_id,
            first_name=kw.pop("first_name", "Someone"),
            last_name=kw.pop("last_name", "Else"),
            phone=kw.pop("phone", "9999999999"),
            appointment_type="Abdominal Ultrasound",
            appointment_datetime=kw.pop("when", datetime(2026, 10, 20, 15, 0, tzinfo=UTC)),
            canceled=kw.pop("canceled", False),
            **kw,
        )

    db_session.add_all(
        [
            appt(
                1,
                "BY-PHONE-FORMATTED",
                phone="(214) 555-0187",
                when=datetime(2026, 10, 21, tzinfo=UTC),
            ),
            appt(2, "BY-PHONE-DIGITS", phone="2145550187", when=datetime(2026, 10, 22, tzinfo=UTC)),
            appt(
                3,
                "BY-PHONE-1-PREFIX",
                phone="+1 214-555-0187",
                when=datetime(2026, 10, 23, tzinfo=UTC),
            ),
            appt(
                4,
                "BY-NAME",
                first_name="MARIA",
                last_name="delgado",
                when=datetime(2026, 10, 19, tzinfo=UTC),
            ),
            appt(
                5,
                "CANCELED-ONE",
                phone="2145550187",
                canceled=True,
                when=datetime(2026, 10, 18, tzinfo=UTC),
            ),
            appt(6, "LINKED-1", phone="2145550187", when=datetime(2026, 10, 17, tzinfo=UTC)),
            appt(7, "NOT-HER", first_name="Kevin", last_name="Liu", phone="9725550162"),
            appt(
                8,
                "SAME-FIRST-NAME-ONLY",
                first_name="Maria",
                last_name="Garcia",
                phone="1112223333",
            ),
        ]
    )
    db_session.flush()

    matches = fax_portal_booking.find_appointment_matches(db_session, db_session, card)

    # Newest first; unrelated people and name-only (first name) matches excluded.
    assert [m.appointment_id for m in matches] == [
        "BY-PHONE-1-PREFIX",
        "BY-PHONE-DIGITS",
        "BY-PHONE-FORMATTED",
        "BY-NAME",
        "CANCELED-ONE",
        "LINKED-1",
    ]
    by_id = {m.appointment_id: m for m in matches}
    assert by_id["CANCELED-ONE"].canceled is True and by_id["BY-NAME"].canceled is False
    assert by_id["LINKED-1"].already_linked is True  # taken by another referral
    assert by_id["BY-NAME"].already_linked is False
    assert other_card.acuity_appointment_id == "LINKED-1"


def test_appointment_matches_need_something_to_match_on(
    db_session: Session, appointment_table: Any
) -> None:
    card = make_card(db_session, first_name=None, last_name=None, phone=None, phone_e164=None)

    assert fax_portal_booking.find_appointment_matches(db_session, db_session, card) == []
