from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.integrations import stripe_client
from app.integrations.stripe_client import StripeApiError, StripeSignatureError
from app.services import image_invoices as svc

SECRET = "whsec_test_secret"


class FakeSession:
    """Repository calls are monkeypatched away, so only commit/rollback matter."""

    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


@dataclass
class FakeRow:
    id: int = 7
    appointment_id: str = "APPT-1"
    status: str = "pending"
    amount_cents: int = 4500
    currency: str = "usd"
    customer_email: str = "patient@example.com"
    stripe_customer_id: str | None = None
    stripe_invoice_id: str | None = None
    hosted_invoice_url: str | None = None
    error_message: str | None = None
    last_stripe_event_id: str | None = None
    sent_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeStripe:
    """Records every Stripe call the service makes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.existing_customer: dict[str, Any] | None = None
        self.item_amount = 4500
        self.item_currency = "usd"
        self.fail_at: str | None = None

        for name in (
            "find_customer_by_email",
            "create_customer",
            "create_draft_invoice",
            "add_invoice_item",
            "finalize_invoice",
            "send_invoice",
            "delete_draft_invoice",
            "void_invoice",
        ):
            monkeypatch.setattr(svc, name, self._make(name))

    def _make(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, {"args": args, **kwargs}))

            if self.fail_at == name:
                raise StripeApiError("boom from stripe")

            if name == "find_customer_by_email":
                return self.existing_customer

            if name == "create_customer":
                return {"id": "cus_new"}

            if name == "create_draft_invoice":
                return {"id": "in_123"}

            if name == "add_invoice_item":
                return {"amount": self.item_amount, "currency": self.item_currency}

            if name == "send_invoice":
                return {"hosted_invoice_url": "https://invoice.stripe.test/i/123"}

            return {}

        return call

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    return FakeStripe(monkeypatch)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "stripe_restricted_api_key_image_invoice", "rk_test_x")
    monkeypatch.setattr(settings, "stripe_image_price_id_image_invoice", "price_test")
    monkeypatch.setattr(settings, "stripe_webhook_secret_image_invoice", SECRET)


@pytest.fixture
def send_path(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patches the repository, audit and name helpers around send_image_invoice."""
    state: dict[str, Any] = {
        "appointment": SimpleNamespace(
            email="  Patient@Example.com ", clinic_id=3, first_name="Jane", last_name="Doe"
        ),
        "active": None,
        "row": FakeRow(),
        "audit": [],
    }

    monkeypatch.setattr(
        svc, "get_appointment_by_appointment_id", lambda db, _id: state["appointment"]
    )
    monkeypatch.setattr(svc, "expire_stale_pending_image_invoices", lambda *a, **k: 0)
    monkeypatch.setattr(svc, "get_active_image_invoice", lambda db, _id: state["active"])
    monkeypatch.setattr(svc, "create_image_invoice", lambda db, **k: state["row"])
    monkeypatch.setattr(svc, "build_patient_name", lambda appointment: "Jane Doe")
    monkeypatch.setattr(svc, "record_user_activity", lambda db, **k: state["audit"].append(k))

    return state


def _send() -> Any:
    return svc.send_image_invoice(
        FakeSession(),  # type: ignore[arg-type]
        FakeSession(),  # type: ignore[arg-type]
        appointment_id="APPT-1",
        actor=SimpleNamespace(id=5),  # type: ignore[arg-type]
        ip_address="203.0.113.9",
        user_agent="pytest",
    )


# --- sending ---------------------------------------------------------------


@pytest.mark.usefixtures("configured")
def test_send_creates_invoice_in_the_right_order(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    result = _send()

    assert stripe.names == [
        "find_customer_by_email",
        "create_customer",
        "create_draft_invoice",
        "add_invoice_item",
        "finalize_invoice",
        "send_invoice",
    ]
    assert result.status == "sent"
    assert result.amount_cents == 4500
    assert result.hosted_invoice_url == "https://invoice.stripe.test/i/123"
    assert send_path["row"].stripe_invoice_id == "in_123"
    assert send_path["row"].sent_at is not None


@pytest.mark.usefixtures("configured")
def test_send_normalizes_email_and_uses_configured_price(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    _send()

    lookup = dict(stripe.calls)["find_customer_by_email"]
    assert lookup["args"] == ("patient@example.com",)

    item = dict(stripe.calls)["add_invoice_item"]
    assert item["price_id"] == "price_test"
    assert item["idempotency_key"].startswith("cc-image-invoice-7-")


@pytest.mark.usefixtures("configured")
def test_send_reuses_existing_stripe_customer(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    stripe.existing_customer = {"id": "cus_existing"}

    _send()

    assert "create_customer" not in stripe.names
    assert send_path["row"].stripe_customer_id == "cus_existing"


@pytest.mark.usefixtures("configured")
def test_send_uses_configured_days_until_due(
    monkeypatch: pytest.MonkeyPatch, stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    monkeypatch.setattr(settings, "stripe_image_invoice_days_until_due", 7)

    _send()

    assert dict(stripe.calls)["create_draft_invoice"]["days_until_due"] == 7


@pytest.mark.usefixtures("configured")
def test_send_writes_an_audit_entry(stripe: FakeStripe, send_path: dict[str, Any]) -> None:
    _send()

    [entry] = send_path["audit"]
    assert entry["action"] == "patient.image_invoice_sent"
    assert entry["resource_id"] == "APPT-1"
    assert entry["details"]["amount_cents"] == 4500


@pytest.mark.usefixtures("configured")
def test_send_does_not_put_phi_in_stripe_metadata(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    _send()

    metadata = dict(stripe.calls)["create_draft_invoice"]["metadata"]
    assert set(metadata) == {"appointment_id", "source"}


@pytest.mark.usefixtures("configured")
def test_unknown_appointment_is_404(stripe: FakeStripe, send_path: dict[str, Any]) -> None:
    send_path["appointment"] = None

    with pytest.raises(HTTPException) as exc:
        _send()

    assert exc.value.status_code == 404
    assert stripe.calls == []


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("email", [None, "", "   ", "not-an-email"])
def test_missing_or_invalid_email_is_422(
    email: str | None, stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    send_path["appointment"].email = email

    with pytest.raises(HTTPException) as exc:
        _send()

    assert exc.value.status_code == 422
    assert stripe.calls == []


def test_unconfigured_stripe_is_503(
    monkeypatch: pytest.MonkeyPatch, stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    monkeypatch.setattr(settings, "stripe_restricted_api_key_image_invoice", None)
    monkeypatch.setattr(settings, "stripe_image_price_id_image_invoice", "price_test")

    with pytest.raises(HTTPException) as exc:
        _send()

    assert exc.value.status_code == 503
    assert stripe.calls == []


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("active_status", ["pending", "sent", "paid"])
def test_existing_active_invoice_is_409(
    active_status: str, stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    send_path["active"] = FakeRow(status=active_status)

    with pytest.raises(HTTPException) as exc:
        _send()

    assert exc.value.status_code == 409
    assert stripe.calls == []


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize(("amount", "currency"), [(5000, "usd"), (4500, "eur"), (0, "usd")])
def test_wrong_price_is_refused_and_draft_deleted(
    amount: int,
    currency: str,
    stripe: FakeStripe,
    send_path: dict[str, Any],
) -> None:
    stripe.item_amount = amount
    stripe.item_currency = currency

    with pytest.raises(HTTPException) as exc:
        _send()

    assert exc.value.status_code == 502
    assert "finalize_invoice" not in stripe.names
    assert "send_invoice" not in stripe.names
    assert "delete_draft_invoice" in stripe.names
    assert send_path["row"].status == "failed"


@pytest.mark.usefixtures("configured")
def test_stripe_error_before_finalize_deletes_the_draft(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    stripe.fail_at = "add_invoice_item"

    with pytest.raises(HTTPException) as exc:
        _send()

    assert exc.value.status_code == 502
    assert "delete_draft_invoice" in stripe.names
    assert "void_invoice" not in stripe.names
    assert send_path["row"].status == "failed"
    assert "boom from stripe" in send_path["row"].error_message


@pytest.mark.usefixtures("configured")
def test_stripe_error_when_sending_voids_the_finalized_invoice(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    stripe.fail_at = "send_invoice"

    with pytest.raises(HTTPException):
        _send()

    assert "void_invoice" in stripe.names
    assert "delete_draft_invoice" not in stripe.names
    assert send_path["row"].status == "failed"


@pytest.mark.usefixtures("configured")
def test_stripe_error_creating_customer_leaves_nothing_to_clean_up(
    stripe: FakeStripe, send_path: dict[str, Any]
) -> None:
    stripe.fail_at = "create_customer"

    with pytest.raises(HTTPException):
        _send()

    assert "delete_draft_invoice" not in stripe.names
    assert "void_invoice" not in stripe.names
    assert send_path["row"].status == "failed"


# --- webhook events --------------------------------------------------------


@pytest.fixture
def webhook_row(monkeypatch: pytest.MonkeyPatch) -> FakeRow:
    row = FakeRow(status="sent", stripe_invoice_id="in_123")
    monkeypatch.setattr(
        svc,
        "get_image_invoice_by_stripe_invoice_id",
        lambda db, invoice_id: row if invoice_id == "in_123" else None,
    )

    return row


def _event(
    event_type: str, *, event_id: str = "evt_1", invoice_id: str = "in_123"
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "created": 1_700_000_000,
        "data": {
            "object": {
                "id": invoice_id,
                "status_transitions": {"paid_at": 1_700_000_100},
            }
        },
    }


def test_paid_event_marks_the_row_paid(webhook_row: FakeRow) -> None:
    db = FakeSession()

    svc.apply_stripe_event(db, _event("invoice.paid"))  # type: ignore[arg-type]

    assert webhook_row.status == "paid"
    assert webhook_row.paid_at == datetime.fromtimestamp(1_700_000_100, tz=UTC)
    assert webhook_row.last_stripe_event_id == "evt_1"
    assert db.committed == 1


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [("invoice.voided", "void"), ("invoice.marked_uncollectible", "uncollectible")],
)
def test_other_terminal_events(event_type: str, expected: str, webhook_row: FakeRow) -> None:
    svc.apply_stripe_event(FakeSession(), _event(event_type))  # type: ignore[arg-type]

    assert webhook_row.status == expected


@pytest.mark.parametrize(
    "event_type",
    ["invoice.payment_failed", "invoice.finalized", "invoice.sent", "customer.created"],
)
def test_events_that_must_not_change_the_row(event_type: str, webhook_row: FakeRow) -> None:
    db = FakeSession()

    svc.apply_stripe_event(db, _event(event_type))  # type: ignore[arg-type]

    assert webhook_row.status == "sent"
    assert db.committed == 0


def test_event_for_an_invoice_we_did_not_create_is_ignored(webhook_row: FakeRow) -> None:
    db = FakeSession()

    svc.apply_stripe_event(db, _event("invoice.paid", invoice_id="in_dashboard"))  # type: ignore[arg-type]

    assert webhook_row.status == "sent"
    assert db.committed == 0


@pytest.mark.parametrize("terminal", ["paid", "void"])
def test_terminal_rows_never_move(terminal: str, webhook_row: FakeRow) -> None:
    webhook_row.status = terminal

    svc.apply_stripe_event(FakeSession(), _event("invoice.marked_uncollectible"))  # type: ignore[arg-type]
    svc.apply_stripe_event(FakeSession(), _event("invoice.voided", event_id="evt_2"))  # type: ignore[arg-type]

    assert webhook_row.status == terminal


def test_replayed_event_is_a_no_op(webhook_row: FakeRow) -> None:
    webhook_row.last_stripe_event_id = "evt_1"
    db = FakeSession()

    svc.apply_stripe_event(db, _event("invoice.marked_uncollectible"))  # type: ignore[arg-type]

    assert webhook_row.status == "sent"
    assert db.committed == 0


# --- signature verification ------------------------------------------------


def _sign(payload: bytes, *, timestamp: int | None = None, secret: str = SECRET) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()

    return f"t={ts},v1={digest}"


@pytest.mark.usefixtures("configured")
def test_valid_signature_returns_the_event() -> None:
    payload = json.dumps({"id": "evt_1", "type": "invoice.paid"}).encode()

    event = stripe_client.verify_webhook_event(payload, _sign(payload))

    assert event["id"] == "evt_1"


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize(
    "header",
    [None, "", "garbage", "t=123", "v1=abc", "t=notanumber,v1=abc"],
)
def test_missing_or_malformed_signature_header_is_rejected(header: str | None) -> None:
    with pytest.raises(StripeSignatureError):
        stripe_client.verify_webhook_event(b"{}", header)


@pytest.mark.usefixtures("configured")
def test_wrong_secret_is_rejected() -> None:
    payload = b'{"id": "evt_1"}'

    with pytest.raises(StripeSignatureError):
        stripe_client.verify_webhook_event(payload, _sign(payload, secret="whsec_other"))


@pytest.mark.usefixtures("configured")
def test_tampered_body_is_rejected() -> None:
    header = _sign(b'{"id": "evt_1"}')

    with pytest.raises(StripeSignatureError):
        stripe_client.verify_webhook_event(b'{"id": "evt_2"}', header)


@pytest.mark.usefixtures("configured")
def test_old_timestamp_is_rejected() -> None:
    payload = b'{"id": "evt_1"}'
    stale = int(time.time()) - stripe_client.WEBHOOK_TOLERANCE_SECONDS - 60

    with pytest.raises(StripeSignatureError):
        stripe_client.verify_webhook_event(payload, _sign(payload, timestamp=stale))


def test_no_secret_configured_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "stripe_webhook_secret_image_invoice", None)
    payload = b'{"id": "evt_1"}'

    with pytest.raises(StripeSignatureError):
        stripe_client.verify_webhook_event(payload, _sign(payload))


# --- routes ----------------------------------------------------------------


@pytest.mark.usefixtures("configured")
def test_webhook_route_rejects_a_bad_signature(client: TestClient) -> None:
    response = client.post(
        "/webhooks/stripe",
        content=b'{"id": "evt_1"}',
        headers={"Stripe-Signature": "t=1,v1=bad"},
    )

    assert response.status_code == 400


@pytest.mark.usefixtures("configured")
def test_webhook_route_rejects_a_missing_signature(client: TestClient) -> None:
    assert client.post("/webhooks/stripe", content=b"{}").status_code == 400


@pytest.mark.usefixtures("configured")
def test_webhook_route_applies_a_signed_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.api.v1.routes.stripe_webhooks.apply_stripe_event",
        lambda db, event: applied.append(event),
    )
    payload = json.dumps(_event("invoice.paid")).encode()

    response = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )

    assert response.status_code == 200
    assert [event["id"] for event in applied] == ["evt_1"]


@pytest.mark.usefixtures("configured")
def test_webhook_route_rejects_an_oversized_body(client: TestClient) -> None:
    response = client.post("/webhooks/stripe", content=b"x" * (1024 * 1024 + 1))

    assert response.status_code == 413


def test_image_invoice_status_requires_login(client: TestClient) -> None:
    assert client.get("/patients/APPT-1/image-invoice").status_code == 401


@pytest.mark.parametrize("headers_fixture", ["admin_auth_headers", "sonographer_auth_headers"])
def test_image_invoice_status_is_open_to_every_patient_role(
    headers_fixture: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.patients.get_image_invoice_status", lambda db, appointment_id: None
    )
    headers = request.getfixturevalue(headers_fixture)

    response = client.get("/patients/APPT-1/image-invoice", headers=headers)

    assert response.status_code == 200
    assert response.json() is None
