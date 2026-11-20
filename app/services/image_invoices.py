from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.stripe_client import (
    StripeApiError,
    add_invoice_item,
    create_customer,
    create_draft_invoice,
    delete_draft_invoice,
    finalize_invoice,
    find_customer_by_email,
    send_invoice,
    void_invoice,
)
from app.models.auth import CCUser
from app.models.image_invoices import CCImageInvoice
from app.repositories.image_invoices import (
    create_image_invoice,
    expire_stale_pending_image_invoices,
    get_active_image_invoice,
    get_image_invoice_by_stripe_invoice_id,
    get_latest_image_invoice,
)
from app.repositories.patients import get_appointment_by_appointment_id
from app.schemas.image_invoices import ImageInvoiceResponse
from app.services.audit import record_user_activity
from app.services.dashboard import build_patient_name

logger = logging.getLogger(__name__)

# The fee is fixed. It is checked against what Stripe actually put on the
# invoice (below), so editing the price in the Stripe dashboard cannot change
# what a patient is billed - the send is refused instead.
IMAGE_INVOICE_AMOUNT_CENTS = 4500
IMAGE_INVOICE_CURRENCY = "usd"

# A `pending` row older than this belongs to a request that died halfway.
PENDING_EXPIRY = timedelta(minutes=10)

ALREADY_EXISTS_DETAIL = {
    "pending": "An image invoice for this appointment is already being created.",
    "sent": "An image invoice has already been sent for this appointment and is awaiting payment.",
    "paid": "The image invoice for this appointment has already been paid.",
}


class _PriceMismatchError(Exception):
    """The Stripe price is not the expected $45.00 USD."""


def _validated_email(raw_email: str | None) -> str:
    candidate = (raw_email or "").strip()

    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "This appointment has no email address. Add the patient's email "
                "to the appointment before sending an invoice."
            ),
        )

    try:
        return validate_email(candidate, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The appointment's email address is not valid: {exc}",
        ) from exc


def _reserve_invoice_row(
    db: Session,
    *,
    appointment_id: str,
    email: str,
    actor: CCUser,
) -> CCImageInvoice:
    """Inserts the `pending` row that claims this appointment's single
    pending/sent/paid slot. The partial unique index settles a race between
    two simultaneous clicks: the loser gets a 409.
    """
    expire_stale_pending_image_invoices(
        db,
        appointment_id=appointment_id,
        created_before=datetime.now(UTC) - PENDING_EXPIRY,
    )

    active = get_active_image_invoice(db, appointment_id)

    if active is not None:
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ALREADY_EXISTS_DETAIL.get(active.status, "An image invoice already exists."),
        )

    try:
        row = create_image_invoice(
            db,
            appointment_id=appointment_id,
            customer_email=email,
            amount_cents=IMAGE_INVOICE_AMOUNT_CENTS,
            currency=IMAGE_INVOICE_CURRENCY,
            sent_by_user_id=actor.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ALREADY_EXISTS_DETAIL["pending"],
        ) from exc

    return row


def _discard_stripe_invoice(invoice_id: str | None, *, finalized: bool) -> None:
    """Best-effort cleanup of an invoice that must not stay in Stripe: a draft
    is deleted, a finalized one (open, payable) is voided. Never raises - the
    caller is already reporting the original failure.
    """
    if invoice_id is None:
        return

    try:
        if finalized:
            void_invoice(invoice_id)
        else:
            delete_draft_invoice(invoice_id)
    except StripeApiError:
        logger.exception(
            "Could not discard Stripe invoice %s after a failed image-invoice send",
            invoice_id,
        )


def _mark_failed(db: Session, row: CCImageInvoice, message: str) -> None:
    row.status = "failed"
    row.error_message = message[:1000]
    db.commit()


def send_image_invoice(
    db: Session,
    prod_db: Session,
    *,
    appointment_id: str,
    actor: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> ImageInvoiceResponse:
    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found.",
        )

    email = _validated_email(appointment.email)

    price_id = settings.stripe_image_price_id_image_invoice

    if not (settings.stripe_restricted_api_key_image_invoice and price_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe invoicing is not configured.",
        )

    row = _reserve_invoice_row(db, appointment_id=appointment_id, email=email, actor=actor)
    key = f"cc-image-invoice-{row.id}"

    stripe_invoice_id: str | None = None
    finalized = False

    try:
        customer = find_customer_by_email(email) or create_customer(
            email=email,
            name=build_patient_name(appointment),
            idempotency_key=f"{key}-customer",
        )
        customer_id = str(customer["id"])

        draft = create_draft_invoice(
            customer_id=customer_id,
            days_until_due=settings.stripe_image_invoice_days_until_due,
            # Internal to Stripe; not printed on the invoice.
            metadata={"appointment_id": appointment_id, "source": "command_center"},
            idempotency_key=f"{key}-invoice",
        )
        stripe_invoice_id = str(draft["id"])

        # Saved before going further, so the webhook can find the row and a
        # crash leaves a trail to the draft.
        row.stripe_customer_id = customer_id
        row.stripe_invoice_id = stripe_invoice_id
        db.commit()

        item = add_invoice_item(
            customer_id=customer_id,
            invoice_id=stripe_invoice_id,
            price_id=price_id,
            idempotency_key=f"{key}-item",
        )

        if (
            item.get("amount") != IMAGE_INVOICE_AMOUNT_CENTS
            or str(item.get("currency", "")).lower() != IMAGE_INVOICE_CURRENCY
        ):
            raise _PriceMismatchError

        finalize_invoice(stripe_invoice_id, idempotency_key=f"{key}-finalize")
        finalized = True

        sent = send_invoice(stripe_invoice_id, idempotency_key=f"{key}-send")
    except _PriceMismatchError:
        _discard_stripe_invoice(stripe_invoice_id, finalized=finalized)
        message = (
            "The Stripe price is not $45.00 USD, so no invoice was sent. "
            "Check STRIPE_IMAGE_PRICE_ID_IMAGE_INVOICE."
        )
        _mark_failed(db, row, message)

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=message,
        ) from None
    except StripeApiError as exc:
        _discard_stripe_invoice(stripe_invoice_id, finalized=finalized)
        _mark_failed(db, row, exc.message)

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not send the invoice: {exc.message}",
        ) from exc

    row.status = "sent"
    row.hosted_invoice_url = sent.get("hosted_invoice_url")
    row.sent_at = datetime.now(UTC)
    row.error_message = None

    record_user_activity(
        db,
        actor_user_id=actor.id,
        action="patient.image_invoice_sent",
        resource_type="appointment",
        resource_id=appointment_id,
        clinic_id=appointment.clinic_id,
        details={
            "source": "command_center",
            "image_invoice_id": row.id,
            "stripe_invoice_id": stripe_invoice_id,
            "amount_cents": IMAGE_INVOICE_AMOUNT_CENTS,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    return ImageInvoiceResponse.model_validate(row)


def get_image_invoice_status(
    db: Session,
    appointment_id: str,
) -> ImageInvoiceResponse | None:
    # Close out a request that died halfway before reporting it, otherwise the
    # UI would show "sending" (and keep the button disabled) forever.
    expire_stale_pending_image_invoices(
        db,
        appointment_id=appointment_id,
        created_before=datetime.now(UTC) - PENDING_EXPIRY,
    )
    db.commit()

    row = get_latest_image_invoice(db, appointment_id)

    return ImageInvoiceResponse.model_validate(row) if row is not None else None


# Webhook event type -> the row status it moves to. invoice.payment_failed is
# deliberately absent: the invoice is still open and payable in Stripe, so
# flipping the row to `failed` would let staff send a second, duplicate
# invoice. invoice.finalized / invoice.sent need no change either - the row is
# already `sent` by the time they arrive.
_EVENT_STATUS = {
    "invoice.paid": "paid",
    "invoice.voided": "void",
    "invoice.marked_uncollectible": "uncollectible",
}

# A row in one of these never moves again, so a late or replayed event cannot
# undo it.
_TERMINAL_STATUSES = frozenset({"paid", "void"})


def apply_stripe_event(db: Session, event: dict[str, Any]) -> None:
    """Applies a verified Stripe webhook event to its image-invoice row.

    The endpoint receives every invoice event on the Stripe account, including
    invoices made by hand in the dashboard; anything that is not one of these
    rows is ignored. Safe to run twice for the same event.
    """
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    new_status = _EVENT_STATUS.get(event_type)

    if new_status is None:
        return

    invoice = (event.get("data") or {}).get("object") or {}
    stripe_invoice_id = invoice.get("id")

    if not stripe_invoice_id:
        return

    row = get_image_invoice_by_stripe_invoice_id(db, str(stripe_invoice_id))

    if row is None:
        return

    if row.last_stripe_event_id == event_id or row.status in _TERMINAL_STATUSES:
        return

    row.status = new_status
    row.last_stripe_event_id = event_id or None

    if new_status == "paid":
        paid_at = (invoice.get("status_transitions") or {}).get("paid_at") or event.get("created")
        row.paid_at = datetime.fromtimestamp(int(paid_at), tz=UTC) if paid_at else datetime.now(UTC)

    db.commit()
