from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.core.config import settings

STRIPE_API_BASE = "https://api.stripe.com/v1"
STRIPE_TIMEOUT_SECONDS = 20.0

# A webhook older than this is rejected, so a captured delivery can't be
# replayed later. Stripe's own libraries default to the same five minutes.
WEBHOOK_TOLERANCE_SECONDS = 300


class StripeApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        # Stripe's own error code (e.g. "resource_missing") when it sent one.
        # None for network or parsing failures.
        self.code = code
        self.http_status = http_status


class StripeSignatureError(Exception):
    """The webhook's Stripe-Signature header did not verify."""


def _require_api_key() -> str:
    if not settings.stripe_restricted_api_key_image_invoice:
        raise RuntimeError(
            "STRIPE_RESTRICTED_API_KEY_IMAGE_INVOICE must be configured to send invoices."
        )

    return settings.stripe_restricted_api_key_image_invoice


def _request(
    method: str,
    path: str,
    *,
    data: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {_require_api_key()}"}

    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    try:
        response = httpx.request(
            method,
            f"{STRIPE_API_BASE}{path}",
            data=data,
            params=params,
            headers=headers,
            timeout=STRIPE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise StripeApiError(f"Could not reach Stripe: {exc.__class__.__name__}") from exc

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.is_success and isinstance(body, dict):
        return body

    error = body.get("error") if isinstance(body, dict) else None

    if isinstance(error, dict):
        raise StripeApiError(
            str(error.get("message") or "Stripe rejected the request."),
            code=error.get("code"),
            http_status=response.status_code,
        )

    raise StripeApiError(
        f"Stripe returned an unexpected response (HTTP {response.status_code}).",
        http_status=response.status_code,
    )


def find_customer_by_email(email: str) -> dict[str, Any] | None:
    result = _request("GET", "/customers", params={"email": email, "limit": 1})
    customers = result.get("data") or []

    return customers[0] if customers else None


def create_customer(
    *,
    email: str,
    name: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _request(
        "POST",
        "/customers",
        data={
            "email": email,
            "name": name,
            "metadata[source]": "command_center",
        },
        idempotency_key=idempotency_key,
    )


def create_draft_invoice(
    *,
    customer_id: str,
    days_until_due: int,
    metadata: dict[str, str],
    idempotency_key: str,
) -> dict[str, Any]:
    """Creates the invoice as a draft. No description/memo is set on purpose:
    it would print on the PDF, and the invoice is meant to carry only the one
    line item. The metadata is internal to Stripe and never shown to the
    patient.
    """
    data: dict[str, Any] = {
        "customer": customer_id,
        "collection_method": "send_invoice",
        "days_until_due": days_until_due,
        # Only the item attached below goes on this invoice - never whatever
        # else is pending on the customer.
        "pending_invoice_items_behavior": "exclude",
        "auto_advance": "false",
    }

    for key, value in metadata.items():
        data[f"metadata[{key}]"] = value

    return _request("POST", "/invoices", data=data, idempotency_key=idempotency_key)


def add_invoice_item(
    *,
    customer_id: str,
    invoice_id: str,
    price_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _request(
        "POST",
        "/invoiceitems",
        data={
            "customer": customer_id,
            "invoice": invoice_id,
            # Newer Stripe API versions nest the price under `pricing`; the
            # old top-level `price` is rejected as an unknown parameter.
            "pricing[price]": price_id,
        },
        idempotency_key=idempotency_key,
    )


def finalize_invoice(invoice_id: str, *, idempotency_key: str) -> dict[str, Any]:
    return _request(
        "POST",
        f"/invoices/{invoice_id}/finalize",
        data={"auto_advance": "false"},
        idempotency_key=idempotency_key,
    )


def send_invoice(invoice_id: str, *, idempotency_key: str) -> dict[str, Any]:
    return _request(
        "POST",
        f"/invoices/{invoice_id}/send",
        idempotency_key=idempotency_key,
    )


def delete_draft_invoice(invoice_id: str) -> None:
    """Best-effort cleanup of a draft that never got sent. Only drafts can be
    deleted; a finalized invoice has to be voided instead.
    """
    _request("DELETE", f"/invoices/{invoice_id}")


def void_invoice(invoice_id: str) -> None:
    _request("POST", f"/invoices/{invoice_id}/void")


def verify_webhook_event(
    payload: bytes,
    signature_header: str | None,
) -> dict[str, Any]:
    """Checks a Stripe webhook's signature and returns the parsed event.

    Fails closed: with no signing secret configured, nothing verifies.
    Header format: "t=<unix time>,v1=<hex hmac>[,v1=...]"; the signed text is
    "<t>.<raw body>" under HMAC-SHA256 with the endpoint's signing secret.
    """
    secret = settings.stripe_webhook_secret_image_invoice

    if not secret:
        raise StripeSignatureError("STRIPE_WEBHOOK_SECRET_IMAGE_INVOICE is not configured.")

    if not signature_header:
        raise StripeSignatureError("Missing Stripe-Signature header.")

    timestamp: str | None = None
    signatures: list[str] = []

    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")

        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise StripeSignatureError("Malformed Stripe-Signature header.")

    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise StripeSignatureError("Malformed Stripe-Signature timestamp.") from exc

    if abs(time.time() - signed_at) > WEBHOOK_TOLERANCE_SECONDS:
        raise StripeSignatureError("Stripe-Signature timestamp is outside the tolerance.")

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()

    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeSignatureError("Stripe-Signature does not match.")

    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise StripeSignatureError("Webhook body is not valid JSON.") from exc

    if not isinstance(event, dict):
        raise StripeSignatureError("Webhook body is not a JSON object.")

    return event
