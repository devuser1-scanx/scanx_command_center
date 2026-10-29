from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ensure_permission, ensure_role, ensure_role_permission
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations import inbox_store, signed_urls, twilio_client
from app.integrations.twilio_client import TwilioApiError, TwilioSendResult
from app.models.auth import CCUserActivityAudit
from app.services import inbox, inbox_catalog

PHONE = "+14695550187"
CONV_ID = "14695550187"
RCS_SID = "HXrcscardtemplate"


class FakeStore:
    """Stands in for the Firestore conversations."""

    def __init__(self) -> None:
        self.conversations: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.recorded: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.read: list[str] = []
        self.fail_record = False

    def add(self, conversation_id: str = CONV_ID, **fields: Any) -> None:
        self.conversations[conversation_id] = {
            "id": conversation_id,
            "patientPhone": f"+{conversation_id}",
            "status": "open",
            "unreadCount": 0,
            **fields,
        }

    def list_conversations(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        return list(self.conversations.values())[:limit]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self.conversations.get(conversation_id)

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.messages.get(conversation_id, [])

    def mark_read(self, conversation_id: str) -> None:
        self.read.append(conversation_id)

    def record_outbound(self, conversation_id: str, **kwargs: Any) -> None:
        if self.fail_record:
            raise RuntimeError("firestore down")

        self.recorded.append({"conversation_id": conversation_id, **kwargs})

    def delete_conversation(self, conversation_id: str) -> None:
        self.deleted.append(conversation_id)


class FakeTwilio:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def send(self, *, to: str, content_sid: str, content_variables: dict[str, str]):
        if self.error:
            raise self.error

        self.sent.append({"to": to, "content_sid": content_sid, "variables": content_variables})

        return TwilioSendResult(message_sid=f"SM{len(self.sent)}", from_number=None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()

    for name in (
        "list_conversations",
        "get_conversation",
        "list_messages",
        "mark_read",
        "record_outbound",
        "delete_conversation",
    ):
        monkeypatch.setattr(inbox_store, name, getattr(fake, name))

    return fake


@pytest.fixture
def twilio(monkeypatch: pytest.MonkeyPatch) -> FakeTwilio:
    fake = FakeTwilio()
    monkeypatch.setattr(twilio_client, "send_content_message", fake.send)

    return fake


@pytest.fixture(autouse=True)
def inbox_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_messaging_service_sid", "MGtest")
    monkeypatch.setattr(settings, "twilio_voice_from_number", "+12145550000")
    monkeypatch.setattr(settings, "twilio_rcs_card_content_sid", RCS_SID)
    monkeypatch.setattr(inbox, "_patient_names", lambda phones: {})
    monkeypatch.setattr(
        signed_urls, "get_signed_media_url", lambda path: f"https://signed.example/{path}"
    )


@pytest.fixture
def agent(db_session: Session) -> Any:
    return make_user(db_session, "sales")


def audit_entries(db: Session) -> list[CCUserActivityAudit]:
    db.expire_all()

    return list(db.scalars(select(CCUserActivityAudit).order_by(CCUserActivityAudit.id)))


def sent_audit(db: Session) -> list[CCUserActivityAudit]:
    return [e for e in audit_entries(db) if e.action.startswith("messages.")]


# ---------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------


def test_nobody_without_the_messages_permissions_gets_in(
    client: TestClient, db_session: Session, store: FakeStore
) -> None:
    ensure_role(db_session, code="nobody", name="Nobody")
    db_session.commit()
    user = make_user(db_session, "nobody")
    headers = auth_headers(client, user)

    assert client.get("/inbox/conversations", headers=headers).status_code == 403
    assert client.post("/inbox/conversations/new", headers=headers, json={}).status_code == 403
    assert client.get("/inbox/conversations").status_code in {401, 403}


def test_viewing_does_not_allow_sending_or_deleting(
    client: TestClient, db_session: Session, store: FakeStore, twilio: FakeTwilio
) -> None:
    role = ensure_role(db_session, code="inbox_viewer", name="Inbox viewer")
    ensure_role_permission(
        db_session,
        role=role,
        permission=ensure_permission(
            db_session, code="messages.view", module="messages", name="View Messages"
        ),
    )
    db_session.commit()
    headers = auth_headers(client, make_user(db_session, "inbox_viewer"))
    store.add()

    assert client.get("/inbox/conversations", headers=headers).status_code == 200
    assert client.get("/inbox/catalog", headers=headers).status_code == 200
    reply = client.post(
        f"/inbox/conversations/{CONV_ID}/reply", headers=headers, json={"body": "hi"}
    )
    new = client.post(
        "/inbox/conversations/new", headers=headers, json={"to_phone": PHONE, "message": "hi"}
    )
    delete = client.delete(f"/inbox/conversations/{CONV_ID}", headers=headers)

    assert (reply.status_code, new.status_code, delete.status_code) == (403, 403, 403)
    assert twilio.sent == [] and store.deleted == []


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


def test_the_list_shows_conversations_with_patient_names(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inbox, "_patient_names", lambda phones: {"4695550187": "Pat Doe"})
    when = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)
    store.add(lastMessage="See you soon", lastMessageAt=when, unreadCount=2)
    store.add("19725550100", lastMessage="Hello")

    body = client.get("/inbox/conversations", headers=auth_headers(client, agent)).json()

    first, second = body["items"]
    assert first["id"] == CONV_ID and first["patient_name"] == "Pat Doe"
    assert first["patient_phone"] == PHONE and first["unread_count"] == 2
    assert first["last_message"] == "See you soon" and first["last_message_at"].startswith(
        "2026-10-29"
    )
    assert second["patient_name"] is None and second["unread_count"] == 0


def test_a_conversation_without_a_stored_phone_falls_back_to_its_id(
    client: TestClient, agent: Any, store: FakeStore
) -> None:
    store.conversations["x1"] = {"id": "x1"}

    [item] = client.get("/inbox/conversations", headers=auth_headers(client, agent)).json()["items"]

    assert item["patient_phone"] == "x1" and item["status"] == "open" and item["unread_count"] == 0


def test_messages_are_listed_and_marking_read_is_separate(
    client: TestClient, agent: Any, store: FakeStore
) -> None:
    store.messages[CONV_ID] = [
        {
            "id": "m1",
            "sid": "SM1",
            "direction": "inbound",
            "body": "Can I move my appointment?",
            "from": PHONE,
            "to": "+12145550000",
            "mediaUrl": None,
            "status": "received",
        },
        {"id": "m2", "direction": "outbound", "body": "Sure", "sentBy": "First1 Last1"},
    ]
    headers = auth_headers(client, agent)

    items = client.get(f"/inbox/conversations/{CONV_ID}/messages", headers=headers).json()["items"]

    assert [m["direction"] for m in items] == ["inbound", "outbound"]
    assert items[0]["from_number"] == PHONE and items[1]["sent_by"] == "First1 Last1"
    assert store.read == []

    assert client.post(f"/inbox/conversations/{CONV_ID}/read", headers=headers).status_code == 204
    assert store.read == [CONV_ID]


@pytest.mark.parametrize("bad_id", ["__x__", ".."])
def test_reserved_conversation_ids_are_not_found(
    client: TestClient, agent: Any, store: FakeStore, bad_id: str
) -> None:
    headers = auth_headers(client, agent)

    assert client.get(f"/inbox/conversations/{bad_id}/messages", headers=headers).status_code == 404


def test_the_catalog_offers_all_exams_but_fewer_for_the_generic_card(
    client: TestClient, agent: Any
) -> None:
    body = client.get("/inbox/catalog", headers=auth_headers(client, agent)).json()

    assert len(body["rcs_exams"]) == 18 and len(body["generic_sales_exams"]) == 15
    assert {"scrotal", "pelvic", "transabdominal"}.isdisjoint(
        option["value"] for option in body["generic_sales_exams"]
    )
    assert body["titles"] == ["Mr.", "Ms."]
    assert [o["value"] for o in body["languages"]] == ["en", "es"]


def test_patient_names_are_optional_when_the_production_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unconfigured() -> Any:
        raise RuntimeError("PROD_DATABASE_URL is not configured.")

    monkeypatch.undo()
    monkeypatch.setattr(inbox, "get_prod_session_factory", unconfigured)

    assert inbox._patient_names([PHONE]) == {}


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------


def test_a_new_conversation_sends_the_text_and_records_it(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    db_session: Session,
) -> None:
    response = client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": "+1 (469) 555-0187", "message": "  Hello there  "},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"conversation_id": CONV_ID, "sid": "SM1"}
    assert twilio.sent == [
        {
            "to": PHONE,
            "content_sid": inbox_catalog.TEXT_CONTENT_SID,
            "variables": {"1": "Hello there"},
        }
    ]

    [recorded] = store.recorded
    assert recorded["conversation_id"] == CONV_ID and recorded["reopen"] is True
    assert recorded["patient_phone"] == PHONE and recorded["last_message"] == "Hello there"
    assert recorded["business_phone"] == "+12145550000"
    assert recorded["message"]["direction"] == "outbound"
    assert recorded["message"]["sentBy"] == f"{agent.first_name} {agent.last_name}"
    assert recorded["message"]["sentByCcUserId"] == agent.id

    [entry] = sent_audit(db_session)
    assert entry.action == "messages.sent" and entry.actor_user_id == agent.id
    assert entry.details == {"kind": "text", "destination_last4": "0187"}
    # Neither the number nor the text is audited.
    assert "4695550187" not in str(entry.details) and "Hello" not in str(entry.details)


def test_ten_digits_without_a_plus_are_a_us_number(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": "469-555-0187", "message": "Hi"},
    )

    assert twilio.sent[0]["to"] == PHONE


@pytest.mark.parametrize("raw", ["12345", "abc", "+1234567890123456789"])
def test_an_invalid_phone_number_is_refused(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio, raw: str
) -> None:
    response = client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": raw, "message": "Hi"},
    )

    assert response.status_code == 422 and twilio.sent == []


def test_a_blank_message_is_refused(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    response = client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": PHONE, "message": "   "},
    )

    assert response.status_code == 422 and twilio.sent == []


def test_a_reply_goes_to_the_conversations_patient_and_keeps_its_status(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    store.add(patientPhone=PHONE)

    response = client.post(
        f"/inbox/conversations/{CONV_ID}/reply",
        headers=auth_headers(client, agent),
        json={"body": "On my way"},
    )

    assert response.status_code == 200, response.text
    assert twilio.sent[0]["to"] == PHONE and twilio.sent[0]["variables"] == {"1": "On my way"}
    assert store.recorded[0]["reopen"] is False


def test_a_reply_to_an_unknown_conversation_is_404(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    response = client.post(
        "/inbox/conversations/999/reply", headers=auth_headers(client, agent), json={"body": "Hi"}
    )

    assert response.status_code == 404 and twilio.sent == []


def test_a_twilio_refusal_is_reported_and_nothing_is_saved_or_audited(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    db_session: Session,
) -> None:
    twilio.error = TwilioApiError("The recipient has opted out", code=21610)

    response = client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": PHONE, "message": "Hi"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "The recipient has opted out"
    assert store.recorded == [] and sent_audit(db_session) == []


def test_sending_answers_503_when_messaging_is_not_configured(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twilio_messaging_service_sid", None)

    response = client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": PHONE, "message": "Hi"},
    )

    assert response.status_code == 503 and store.recorded == []


def test_a_sent_message_that_cannot_be_saved_says_so(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    db_session: Session,
) -> None:
    store.fail_record = True

    response = client.post(
        "/inbox/conversations/new",
        headers=auth_headers(client, agent),
        json={"to_phone": PHONE, "message": "Hi"},
    )

    assert response.status_code == 500
    assert "could not be saved" in response.json()["detail"]
    assert len(twilio.sent) == 1 and sent_audit(db_session) == []


# ---------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------


def test_the_exam_card_fills_the_template_and_is_recorded(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    db_session: Session,
) -> None:
    response = client.post(
        "/inbox/send-rcs-card",
        headers=auth_headers(client, agent),
        json={
            "exam": "Thyroid",
            "title": "Ms.",
            "patient_name": "Pat Doe",
            "doctor_name": "Dr. Lee",
            "patient_phone": PHONE,
        },
    )

    assert response.status_code == 200, response.text
    [sent] = twilio.sent
    assert sent["content_sid"] == RCS_SID
    assert sent["variables"] == {
        "1": "Ms.",
        "2": "Pat Doe",
        "3": "Dr. Lee",
        "4": "https://signed.example/sales/thyroid.jpg",
        "5": inbox_catalog.EXAMS_BY_KEY["thyroid"].booking_link,
        "6": "Thyroid",
    }
    [recorded] = store.recorded
    assert recorded["last_message"] == "RCS Card Sent: Ms. Pat Doe — thyroid"
    assert recorded["reopen"] is False
    assert recorded["message"]["type"] == "rcs-card"
    assert recorded["message"]["meta"]["exam"] == "thyroid"
    [entry] = sent_audit(db_session)
    assert entry.details["kind"] == "rcs_card" and "Pat" not in str(entry.details)


def test_an_exam_with_its_own_template_uses_it_and_others_use_the_default(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twilio_rcs_card_content_sid_fibroscan", "HXfibroscan")
    headers = auth_headers(client, agent)

    for exam in ("fibroscan", "thyroid"):
        client.post(
            "/inbox/send-rcs-card",
            headers=headers,
            json={
                "exam": exam,
                "title": "Mr.",
                "patient_name": "Pat Doe",
                "doctor_name": "Dr. Lee",
                "patient_phone": PHONE,
            },
        )

    assert [sent["content_sid"] for sent in twilio.sent] == ["HXfibroscan", RCS_SID]


@pytest.mark.parametrize(
    "changes",
    [{"exam": "mri"}, {"title": "Dr."}],
)
def test_the_exam_card_refuses_unknown_exams_and_titles(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio, changes: dict[str, str]
) -> None:
    payload = {
        "exam": "thyroid",
        "title": "Ms.",
        "patient_name": "Pat Doe",
        "doctor_name": "Dr. Lee",
        "patient_phone": PHONE,
        **changes,
    }

    response = client.post(
        "/inbox/send-rcs-card", headers=auth_headers(client, agent), json=payload
    )

    assert response.status_code == 422 and twilio.sent == []


def test_the_exam_card_needs_its_template_configured(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twilio_rcs_card_content_sid", None)

    response = client.post(
        "/inbox/send-rcs-card",
        headers=auth_headers(client, agent),
        json={
            "exam": "thyroid",
            "title": "Mr.",
            "patient_name": "Pat Doe",
            "doctor_name": "Dr. Lee",
            "patient_phone": PHONE,
        },
    )

    assert response.status_code == 503 and twilio.sent == []


def test_a_missed_call_card_uses_the_template_of_its_language(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    response = client.post(
        "/inbox/send-missed-call-card",
        headers=auth_headers(client, agent),
        json={"patient_phone": PHONE, "language": "ES"},
    )

    assert response.status_code == 200, response.text
    assert twilio.sent[0]["content_sid"] == inbox_catalog.MISSED_CALL_CONTENT_SIDS["es"]
    assert twilio.sent[0]["variables"] == {"1": "https://signed.example/sales/missed_call.jpg"}
    assert store.recorded[0]["last_message"] == "RCS Card Sent: Missed Call (ES)"
    assert store.recorded[0]["message"]["meta"] == {"card": "missed_call", "language": "es"}


def test_a_missed_call_card_refuses_an_unknown_language(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    response = client.post(
        "/inbox/send-missed-call-card",
        headers=auth_headers(client, agent),
        json={"patient_phone": PHONE, "language": "fr"},
    )

    assert response.status_code == 422 and twilio.sent == []


def test_the_generic_sales_card_sends_the_image_and_booking_link(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    response = client.post(
        "/inbox/send-generic-sales-card",
        headers=auth_headers(client, agent),
        json={"exam": "fibroscan", "patient_phone": PHONE},
    )

    assert response.status_code == 200, response.text
    assert twilio.sent[0]["content_sid"] == inbox_catalog.GENERIC_SALES_CONTENT_SID
    assert twilio.sent[0]["variables"] == {
        "1": "https://signed.example/sales/fibroscan.jpg",
        "2": inbox_catalog.EXAMS_BY_KEY["fibroscan"].booking_link,
    }
    assert store.recorded[0]["last_message"] == "Generic Sales Card Sent: fibroscan"


def test_the_generic_sales_card_is_not_offered_for_every_exam(
    client: TestClient, agent: Any, store: FakeStore, twilio: FakeTwilio
) -> None:
    response = client.post(
        "/inbox/send-generic-sales-card",
        headers=auth_headers(client, agent),
        json={"exam": "scrotal", "patient_phone": PHONE},
    )

    assert response.status_code == 422 and twilio.sent == []


def test_a_card_whose_image_cannot_be_signed_is_a_502(
    client: TestClient,
    agent: Any,
    store: FakeStore,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(path: str) -> str:
        raise signed_urls.SignedUrlError("Failed to generate media URL.")

    monkeypatch.setattr(signed_urls, "get_signed_media_url", fail)

    response = client.post(
        "/inbox/send-generic-sales-card",
        headers=auth_headers(client, agent),
        json={"exam": "fibroscan", "patient_phone": PHONE},
    )

    assert response.status_code == 502 and twilio.sent == []


# ---------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------


def test_deleting_removes_the_conversation_and_audits_without_the_number(
    client: TestClient, agent: Any, store: FakeStore, db_session: Session
) -> None:
    store.add()

    response = client.delete(f"/inbox/conversations/{CONV_ID}", headers=auth_headers(client, agent))

    assert response.status_code == 204 and store.deleted == [CONV_ID]
    [entry] = sent_audit(db_session)
    assert entry.action == "messages.conversation_deleted"
    assert entry.details == {"destination_last4": "0187"}


def test_deleting_an_unknown_conversation_is_404(
    client: TestClient, agent: Any, store: FakeStore
) -> None:
    response = client.delete("/inbox/conversations/999", headers=auth_headers(client, agent))

    assert response.status_code == 404 and store.deleted == []


# ---------------------------------------------------------------------
# Helpers and the Firestore wrapper
# ---------------------------------------------------------------------


def test_phone_normalization() -> None:
    assert inbox.normalize_patient_phone("+1 (469) 555-0187") == (PHONE, CONV_ID)
    assert inbox.normalize_patient_phone("14695550187") == (PHONE, CONV_ID)
    assert inbox.normalize_patient_phone("+442071838750") == ("+442071838750", "442071838750")

    with pytest.raises(HTTPException):
        inbox.normalize_patient_phone("555")


class FakeDocument:
    def __init__(self, existing: bool) -> None:
        self.existing = existing
        self.calls: list[tuple[str, Any]] = []
        self.added: list[dict[str, Any]] = []

    def get(self, **_options: Any) -> Any:
        return type("Snapshot", (), {"exists": self.existing})()

    def set(self, data: dict[str, Any], **_options: Any) -> None:
        self.calls.append(("set", data))

    def update(self, data: dict[str, Any], **_options: Any) -> None:
        self.calls.append(("update", data))

    def collection(self, name: str) -> Any:
        assert name == "messages"

        return type(
            "Messages", (), {"add": lambda _self, data, **_options: self.added.append(data)}
        )()


def with_document(monkeypatch: pytest.MonkeyPatch, document: FakeDocument) -> None:
    monkeypatch.setattr(inbox_store, "_conversation", lambda conversation_id: document)


def test_the_store_creates_a_new_conversation_with_its_first_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = FakeDocument(existing=False)
    with_document(monkeypatch, document)

    inbox_store.record_outbound(
        CONV_ID,
        patient_phone=PHONE,
        last_message="Hi",
        message={"sid": "SM1", "direction": "outbound"},
        reopen=False,
        business_phone="+12145550000",
    )

    [(action, data)] = document.calls
    assert action == "set"
    assert data["patientPhone"] == PHONE and data["businessPhone"] == "+12145550000"
    assert data["status"] == "open" and data["unreadCount"] == 0 and data["assignedTo"] is None
    assert data["lastMessage"] == "Hi"
    assert document.added[0]["sid"] == "SM1" and "createdAt" in document.added[0]


@pytest.mark.parametrize(("reopen", "status_set"), [(True, True), (False, False)])
def test_the_store_updates_an_existing_conversation(
    monkeypatch: pytest.MonkeyPatch, reopen: bool, status_set: bool
) -> None:
    document = FakeDocument(existing=True)
    with_document(monkeypatch, document)

    inbox_store.record_outbound(
        CONV_ID,
        patient_phone=PHONE,
        last_message="Hi",
        message={"sid": "SM1"},
        reopen=reopen,
        business_phone=None,
    )

    [(action, data)] = document.calls
    assert action == "update" and data["lastMessage"] == "Hi"
    assert ("status" in data) is status_set
    assert "unreadCount" not in data
