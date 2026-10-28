from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_card, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.fax_portal import CCFpActivityLog, CCFpCallLog, CCFpContactOptout, CCFpReferralBoard

PATIENT = "+12145550187"
VOICE_NUMBER = "+14695550111"
BASE = "https://api.example.com"
TOKEN = "unit-test-auth-token"
KEY_SID = "SKunittestkey"
KEY_SECRET = "unit-test-api-key-secret-0123456789"
APP_SID = "APunittestapp"


@pytest.fixture(autouse=True)
def voice_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", TOKEN)
    monkeypatch.setattr(settings, "twilio_voice_from_number", VOICE_NUMBER)
    monkeypatch.setattr(settings, "twilio_api_key_sid", KEY_SID)
    monkeypatch.setattr(settings, "twilio_api_key_secret", KEY_SECRET)
    monkeypatch.setattr(settings, "twilio_twiml_app_sid", APP_SID)
    monkeypatch.setattr(settings, "public_api_base_url", BASE)


@pytest.fixture
def agent(db_session: Session) -> Any:
    # No phone on the user: browser calling must not need one.
    return make_user(db_session, "sales")


def card(db: Session, **kwargs: Any) -> CCFpReferralBoard:
    kwargs.setdefault("status", "ready_for_contact")
    kwargs.setdefault("phone", PATIENT)
    kwargs.setdefault("phone_e164", PATIENT)
    row = make_card(db, **kwargs)
    db.commit()

    return row


def dial(client: TestClient, headers: dict[str, str], card_id: int):
    return client.post(f"/fax-portal/referrals/{card_id}/calls/dial", headers=headers)


def case(client: TestClient, headers: dict[str, str], card_id: int) -> dict[str, Any]:
    response = client.get(f"/fax-portal/referrals/{card_id}/case", headers=headers)

    assert response.status_code == 200, response.text

    return response.json()


def calls(db: Session) -> list[CCFpCallLog]:
    db.expire_all()

    return list(db.scalars(select(CCFpCallLog).order_by(CCFpCallLog.id)))


def actions(db: Session) -> list[str]:
    return list(db.scalars(select(CCFpActivityLog.action).order_by(CCFpActivityLog.id)))


# ---------------------------------------------------------------------
# Starting a call
# ---------------------------------------------------------------------


def test_dialling_logs_the_call_and_returns_a_token_for_the_browser(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    row = card(db_session)

    response = dial(client, auth_headers(client, agent), row.id)

    assert response.status_code == 200, response.text
    body = response.json()
    [call] = calls(db_session)
    assert body["call_id"] == call.id
    # Written like a hand-logged call, from the organisation's number, not yet with a sid.
    assert (call.referral_id, call.user_id, call.to_number) == (row.id, agent.id, PATIENT)
    assert call.from_number == VOICE_NUMBER and call.result is None
    assert (call.twilio_status, call.twilio_call_sid) == ("starting", None)
    start = db_session.scalar(select(CCFpActivityLog).where(CCFpActivityLog.action == "call_start"))
    assert start.details["source"] == "twilio"


def test_the_token_is_signed_with_the_api_key_and_only_lets_the_browser_start_calls(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    row = card(db_session)

    token = dial(client, auth_headers(client, agent), row.id).json()["token"]

    claims = jwt.decode(token, KEY_SECRET, algorithms=["HS256"], options={"verify_aud": False})
    assert (claims["iss"], claims["sub"]) == (KEY_SID, "ACtest")
    assert claims["grants"]["identity"] == f"user-{agent.id}"
    assert claims["grants"]["voice"] == {"outgoing": {"application_sid": APP_SID}}
    assert 0 < claims["exp"] - claims["iat"] <= 3600
    # No patient data in what the browser holds.
    assert PATIENT not in token and PATIENT not in str(claims)
    assert jwt.get_unverified_header(token)["cty"] == "twilio-fpa;v=1"


def test_the_case_says_whether_calling_is_set_up(
    client: TestClient, db_session: Session, agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = card(db_session)
    headers = auth_headers(client, agent)

    ready = case(client, headers, row.id)
    assert ready["call_service_connected"] is True
    assert ready["can_dial"] is True and ready["dial_blocked_reason"] is None

    monkeypatch.setattr(settings, "twilio_twiml_app_sid", None)
    off = case(client, headers, row.id)
    assert off["call_service_connected"] is False and off["can_dial"] is False
    assert "not set up" in off["dial_blocked_reason"]


def test_a_referral_that_cannot_be_called_gives_that_reason(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    row = card(db_session, status="appointment_booked")

    detail = case(client, auth_headers(client, agent), row.id)

    assert detail["can_dial"] is False
    assert detail["dial_blocked_reason"] == "This referral is closed."


def test_unconfigured_calling_fails_cleanly(
    client: TestClient, db_session: Session, agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_api_key_secret", None)
    row = card(db_session)

    response = dial(client, auth_headers(client, agent), row.id)

    assert response.status_code == 502 and "not set up" in response.json()["detail"]
    assert "TWILIO" not in response.text and calls(db_session) == []


def test_dialling_follows_the_same_rules_as_any_call(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    headers = auth_headers(client, agent)
    optout = CCFpContactOptout(phone_e164="+12145550999")
    optout.dnc_at = datetime.now(UTC)
    db_session.add(optout)
    blocked = card(db_session, phone_e164="+12145550999")
    review = card(db_session, status="review_required")
    no_number = card(db_session, phone=None, phone_e164=None)
    at_limit = card(db_session, qualifying_call_count=5, status="follow_up_due")
    in_progress = card(db_session)

    for refused in (blocked, review, no_number):
        assert dial(client, headers, refused.id).status_code == 422
    assert dial(client, headers, at_limit.id).status_code in (409, 422)
    assert calls(db_session) == []

    # Only one call can wait for its result: a second click is refused.
    assert dial(client, headers, in_progress.id).status_code == 200
    second = dial(client, headers, in_progress.id)
    assert second.status_code == 409 and "in progress" in second.json()["detail"]
    assert len(calls(db_session)) == 1


def test_a_browser_that_never_connected_does_not_block_the_next_call(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    row = card(db_session)
    headers = auth_headers(client, agent)
    assert dial(client, headers, row.id).status_code == 200
    [first] = calls(db_session)

    # Within two minutes it still counts as in progress.
    assert dial(client, headers, row.id).status_code == 409

    # The microphone was refused and the tab left open: after two minutes it is gone.
    first.started_at = datetime.now(UTC) - timedelta(minutes=3)
    db_session.commit()
    assert case(client, headers, row.id)["calls"] == []
    assert dial(client, headers, row.id).status_code == 200
    assert [c.id for c in calls(db_session)] != [first.id]
    assert "call_abandoned" in actions(db_session)


def test_the_browser_can_abandon_a_call_it_could_not_start(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    row = card(db_session)
    headers = auth_headers(client, agent)
    call_id = dial(client, headers, row.id).json()["call_id"]

    response = client.post(f"/fax-portal/calls/{call_id}/abandon", headers=headers)

    assert response.status_code == 204
    assert calls(db_session) == [] and "call_abandoned" in actions(db_session)
    assert dial(client, headers, row.id).status_code == 200


def test_only_the_caller_can_abandon_and_only_before_twilio_has_the_call(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    row = card(db_session)
    other = make_user(db_session, "sales")
    call_id = dial(client, auth_headers(client, agent), row.id).json()["call_id"]

    stranger = client.post(
        f"/fax-portal/calls/{call_id}/abandon", headers=auth_headers(client, other)
    )
    assert stranger.status_code == 404

    [call] = calls(db_session)
    call.twilio_call_sid = "CAlive"
    db_session.commit()
    connected = client.post(
        f"/fax-portal/calls/{call_id}/abandon", headers=auth_headers(client, agent)
    )
    assert connected.status_code == 404 and len(calls(db_session)) == 1


# ---------------------------------------------------------------------
# Twilio's webhooks
# ---------------------------------------------------------------------


def sign(path: str, params: dict[str, str]) -> str:
    signed = f"{BASE}{path}" + "".join(f"{key}{params[key]}" for key in sorted(params))

    return base64.b64encode(
        hmac.new(TOKEN.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()


def post(client: TestClient, path: str, params: dict[str, str], *, signature: str | None = None):
    return client.post(
        path,
        data=params,
        headers={"X-Twilio-Signature": signature if signature is not None else sign(path, params)},
    )


CLIENT = "/fax-portal/webhooks/twilio/voice/client"
DIAL_DONE = "/fax-portal/webhooks/twilio/voice/dial-done"
STATUS = "/fax-portal/webhooks/twilio/voice/status"
SID = "CAbrowserleg"


@pytest.fixture
def started(client: TestClient, db_session: Session, agent: Any) -> tuple[CCFpCallLog, Any]:
    row = card(db_session)

    assert dial(client, auth_headers(client, agent), row.id).status_code == 200

    [call] = calls(db_session)

    return call, agent


def twilio_asks(call: CCFpCallLog, user: Any, **extra: str) -> dict[str, str]:
    """What Twilio posts to the TwiML App's Voice URL when the browser connects."""
    return {"CallSid": SID, "From": f"client:user-{user.id}", "CallId": str(call.id), **extra}


def test_when_the_browser_connects_the_patient_is_dialled_from_the_org_number(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started

    # A "To" the caller made up is ignored: the number comes from the call row.
    response = post(client, CLIENT, twilio_asks(call, user, To="+19995550000"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/xml")
    body = response.text
    assert f"<Number>{PATIENT}</Number>" in body and "+19995550000" not in body
    assert f'callerId="{VOICE_NUMBER}"' in body and 'answerOnBridge="true"' in body
    assert f'action="{BASE}{DIAL_DONE}"' in body
    [row] = calls(db_session)
    assert (row.twilio_call_sid, row.twilio_status) == (SID, "initiated")


def test_a_call_row_can_only_be_used_once(
    client: TestClient, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started

    assert "<Number>" in post(client, CLIENT, twilio_asks(call, user)).text
    again = post(client, CLIENT, twilio_asks(call, user, CallSid="CAsecond"))

    assert "<Number>" not in again.text and "<Hangup/>" in again.text


def test_another_users_browser_cannot_use_someone_elses_call_row(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, _ = started
    other = make_user(db_session, "sales")

    body = post(client, CLIENT, twilio_asks(call, other)).text

    assert "<Number>" not in body and "<Hangup/>" in body
    assert calls(db_session)[0].twilio_call_sid is None


def test_an_unknown_or_finished_call_is_hung_up_not_dialled(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    caller = f"client:user-{user.id}"

    unknown = post(client, CLIENT, {"CallSid": SID, "From": caller, "CallId": "999999"})
    assert "<Number>" not in unknown.text and "<Hangup/>" in unknown.text
    junk = post(client, CLIENT, {"CallSid": SID, "From": caller})
    assert "<Number>" not in junk.text

    call.result = "no_answer"
    db_session.commit()
    assert "<Number>" not in post(client, CLIENT, twilio_asks(call, user)).text


def test_a_number_blocked_after_the_call_started_is_not_dialled(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    optout = CCFpContactOptout(phone_e164=PATIENT)
    optout.dnc_at = datetime.now(UTC)
    db_session.add(optout)
    db_session.commit()

    body = post(client, CLIENT, twilio_asks(call, user)).text

    assert "<Number>" not in body and "<Hangup/>" in body


@pytest.mark.parametrize("path", [CLIENT, DIAL_DONE, STATUS])
def test_voice_webhooks_need_twilios_signature(
    client: TestClient, started: tuple[CCFpCallLog, Any], path: str
) -> None:
    call, user = started
    params = twilio_asks(call, user)

    assert post(client, path, params, signature="forged").status_code == 403
    assert client.post(path, data=params).status_code == 403


def connect(client: TestClient, call: CCFpCallLog, user: Any) -> None:
    assert "<Number>" in post(client, CLIENT, twilio_asks(call, user)).text


def test_the_patient_not_answering_is_recorded_and_suggests_a_result(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    connect(client, call, user)

    response = post(client, DIAL_DONE, {"CallSid": SID, "DialCallStatus": "no-answer"})

    assert response.status_code == 200 and "<Hangup/>" in response.text
    assert calls(db_session)[0].twilio_status == "patient:no-answer"
    detail = case(client, auth_headers(client, user), call.referral_id)
    assert detail["open_call_source"] == "twilio"
    assert detail["open_call_status"] == "patient:no-answer"
    assert detail["suggested_result"] == "no_answer"
    # Only a suggestion: the result is still the agent's to record.
    assert calls(db_session)[0].result is None


def test_an_answered_call_records_talk_time_and_suggests_nothing(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    connect(client, call, user)

    post(
        client,
        DIAL_DONE,
        {"CallSid": SID, "DialCallStatus": "completed", "DialCallDuration": "95"},
    )

    [row] = calls(db_session)
    assert (row.twilio_status, row.duration_s) == ("patient:completed", 95)
    assert row.answered_at is not None
    assert case(client, auth_headers(client, user), call.referral_id)["suggested_result"] is None


def test_the_call_result_is_recorded_on_a_browser_call_like_any_other(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    connect(client, call, user)
    post(client, DIAL_DONE, {"CallSid": SID, "DialCallStatus": "no-answer"})

    response = client.post(
        f"/fax-portal/calls/{call.id}/result",
        json={"result": "no_answer"},
        headers=auth_headers(client, user),
    )

    assert response.status_code == 200, response.text
    assert response.json()["qualifying_call_count"] == 1
    assert response.json()["status"] == "follow_up_due"


def test_progress_only_moves_forward_and_never_hides_the_patients_outcome(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    connect(client, call, user)

    for status in ("ringing", "in-progress", "ringing"):
        assert post(client, STATUS, {"CallSid": SID, "CallStatus": status}).status_code == 204

    assert calls(db_session)[0].twilio_status == "in-progress"

    post(client, DIAL_DONE, {"CallSid": SID, "DialCallStatus": "busy"})
    post(client, STATUS, {"CallSid": SID, "CallStatus": "completed"})

    [row] = calls(db_session)
    assert row.twilio_status == "patient:busy"
    assert row.ended_at is not None


def test_a_browser_leg_that_fails_leaves_no_attempt_behind(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    referral_id = call.referral_id
    connect(client, call, user)

    assert post(client, STATUS, {"CallSid": SID, "CallStatus": "failed"}).status_code == 204

    # Not an attempt on the patient: no row, nothing blocking, nothing counted.
    assert calls(db_session) == []
    row = db_session.get(CCFpReferralBoard, referral_id)
    assert (row.qualifying_call_count, row.total_call_count) == (0, 0)
    assert "call_agent_unreachable" in actions(db_session)
    assert case(client, auth_headers(client, user), referral_id)["can_dial"] is True


def test_a_late_status_does_not_delete_a_call_the_patient_took(
    client: TestClient, db_session: Session, started: tuple[CCFpCallLog, Any]
) -> None:
    call, user = started
    connect(client, call, user)
    post(
        client,
        DIAL_DONE,
        {"CallSid": SID, "DialCallStatus": "completed", "DialCallDuration": "30"},
    )

    post(client, STATUS, {"CallSid": SID, "CallStatus": "failed"})

    assert len(calls(db_session)) == 1


def test_updates_for_an_unknown_call_are_acknowledged(client: TestClient) -> None:
    assert post(client, STATUS, {"CallSid": "CAnone", "CallStatus": "ringing"}).status_code == 204
    assert post(client, DIAL_DONE, {"CallSid": "CAnone"}).status_code == 200
