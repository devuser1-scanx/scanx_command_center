from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.fax_portal import CCFpComprehendPayload, CCFpFax, CCFpReferralBoard

URL = "/fax-portal/webhooks/comprehend"
TOKEN = "unit-test-webhook-token-123"
FIXTURE = Path(__file__).parent / "fixtures" / "comprehend_sample.json"


@pytest.fixture
def body() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(autouse=True)
def webhook_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "comprehend_webhook_token", TOKEN)
    monkeypatch.setattr(settings, "comprehend_webhook_hmac_secret", None)


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


JSON_HEADERS = {"Content-Type": "application/json"}


def test_valid_delivery_creates_fax_and_card(
    client: TestClient, db_session: Session, body: bytes
) -> None:
    response = client.post(URL, content=body, headers={**JSON_HEADERS, "X-Api-Key": TOKEN})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["parsed"] is True
    assert data["duplicate"] is False
    assert data["referrals"] == 1

    assert count(db_session, CCFpFax) == 1
    assert count(db_session, CCFpReferralBoard) == 1


def test_response_never_echoes_phi(client: TestClient, body: bytes) -> None:
    response = client.post(URL, content=body, headers={**JSON_HEADERS, "X-Api-Key": TOKEN})

    assert "Doe" not in response.text
    assert "John" not in response.text


@pytest.mark.parametrize(
    "auth",
    [
        {"headers": {"Authorization": f"Bearer {TOKEN}"}},
        {"headers": {"X-Api-Key": TOKEN}},
        {"params": {"token": TOKEN}},
    ],
    ids=["bearer", "x-api-key", "query-token"],
)
def test_all_three_token_styles_are_accepted(
    client: TestClient, db_session: Session, body: bytes, auth: dict
) -> None:
    response = client.post(
        URL,
        content=body,
        params=auth.get("params"),
        headers={**JSON_HEADERS, **auth.get("headers", {})},
    )

    assert response.status_code == 200
    assert count(db_session, CCFpFax) == 1


@pytest.mark.parametrize(
    "headers",
    [{"Authorization": "Bearer "}, {"Authorization": "Bearer wrong"}, {"X-Api-Key": "wrong"}],
    ids=["empty-bearer", "wrong-bearer", "wrong-api-key"],
)
def test_a_bad_header_does_not_hide_a_valid_query_token(
    client: TestClient, db_session: Session, body: bytes, headers: dict[str, str]
) -> None:
    # e.g. a connector set to "Bearer token" with the token box left empty,
    # but the token present in the URL.
    response = client.post(
        URL, content=body, params={"token": TOKEN}, headers={**JSON_HEADERS, **headers}
    )

    assert response.status_code == 200
    assert count(db_session, CCFpFax) == 1


def test_wrong_credentials_everywhere_are_still_rejected(client: TestClient, body: bytes) -> None:
    response = client.post(
        URL,
        content=body,
        params={"token": "wrong"},
        headers={**JSON_HEADERS, "Authorization": "Bearer also-wrong"},
    )

    assert response.status_code == 401


def test_retry_is_acknowledged_as_duplicate(
    client: TestClient, db_session: Session, body: bytes
) -> None:
    headers = {**JSON_HEADERS, "X-Api-Key": TOKEN}

    client.post(URL, content=body, headers=headers)
    retry = client.post(URL, content=body, headers=headers)

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert count(db_session, CCFpFax) == 1
    assert count(db_session, CCFpReferralBoard) == 1
    assert count(db_session, CCFpComprehendPayload) == 2


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Api-Key": "wrong"}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}],
    ids=["missing", "wrong-api-key", "wrong-bearer", "wrong-scheme"],
)
def test_bad_credentials_are_rejected_and_nothing_is_stored(
    client: TestClient, db_session: Session, body: bytes, headers: dict[str, str]
) -> None:
    response = client.post(URL, content=body, headers={**JSON_HEADERS, **headers})

    assert response.status_code == 401
    assert count(db_session, CCFpComprehendPayload) == 0
    assert count(db_session, CCFpFax) == 0


def test_fails_closed_when_no_token_is_configured(
    client: TestClient, db_session: Session, body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "comprehend_webhook_token", None)

    response = client.post(URL, content=body, headers={**JSON_HEADERS, "X-Api-Key": ""})

    assert response.status_code == 503
    assert count(db_session, CCFpComprehendPayload) == 0


def test_oversized_body_is_rejected(
    client: TestClient, db_session: Session, body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "comprehend_webhook_max_body_bytes", 100)

    response = client.post(URL, content=body, headers={**JSON_HEADERS, "X-Api-Key": TOKEN})

    assert response.status_code == 413
    assert count(db_session, CCFpComprehendPayload) == 0


def test_malformed_body_is_acknowledged_and_kept(client: TestClient, db_session: Session) -> None:
    response = client.post(URL, content=b"{not json", headers={**JSON_HEADERS, "X-Api-Key": TOKEN})

    # 200 so WestFax does not retry forever; the delivery is kept for review.
    assert response.status_code == 200
    assert response.json()["parsed"] is False

    row = db_session.scalar(select(CCFpComprehendPayload))
    assert row is not None
    assert row.parse_error == "Request body is not a JSON object."
    assert count(db_session, CCFpReferralBoard) == 1


# ---------------------------------------------------------------------
# Optional HMAC signature
# ---------------------------------------------------------------------


def _signature(secret: str, body: bytes) -> bytes:
    return hmac.new(secret.encode(), body, hashlib.sha256).digest()


@pytest.mark.parametrize(
    "encode",
    [
        lambda d: d.hex(),
        lambda d: "sha256=" + d.hex(),
        lambda d: base64.b64encode(d).decode(),
    ],
    ids=["hex", "prefixed-hex", "base64"],
)
def test_valid_hmac_signature_is_accepted(
    client: TestClient, body: bytes, monkeypatch: pytest.MonkeyPatch, encode
) -> None:
    monkeypatch.setattr(settings, "comprehend_webhook_hmac_secret", "hmac-secret")

    response = client.post(
        URL,
        content=body,
        headers={
            **JSON_HEADERS,
            "X-Api-Key": TOKEN,
            "X-Webhook-Signature": encode(_signature("hmac-secret", body)),
        },
    )

    assert response.status_code == 200


def test_missing_or_wrong_hmac_signature_is_rejected(
    client: TestClient, db_session: Session, body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "comprehend_webhook_hmac_secret", "hmac-secret")
    headers = {**JSON_HEADERS, "X-Api-Key": TOKEN}

    missing = client.post(URL, content=body, headers=headers)
    wrong = client.post(
        URL,
        content=body,
        headers={**headers, "X-Webhook-Signature": _signature("other", body).hex()},
    )
    tampered = client.post(
        URL,
        content=body + b" ",
        headers={**headers, "X-Webhook-Signature": _signature("hmac-secret", body).hex()},
    )

    assert (missing.status_code, wrong.status_code, tampered.status_code) == (401, 401, 401)
    assert count(db_session, CCFpComprehendPayload) == 0


def test_hmac_alone_is_not_enough_without_the_token(
    client: TestClient, body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "comprehend_webhook_hmac_secret", "hmac-secret")

    response = client.post(
        URL,
        content=body,
        headers={
            **JSON_HEADERS,
            "X-Webhook-Signature": _signature("hmac-secret", body).hex(),
        },
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------


def test_get_probe_confirms_reachability_without_a_token(client: TestClient) -> None:
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_payload_json_round_trips_exactly(
    client: TestClient, db_session: Session, body: bytes
) -> None:
    client.post(URL, content=body, headers={**JSON_HEADERS, "X-Api-Key": TOKEN})

    row = db_session.scalar(select(CCFpComprehendPayload))

    assert row is not None
    assert row.payload == json.loads(body)
    assert row.payload_sha256 == hashlib.sha256(body).hexdigest()
