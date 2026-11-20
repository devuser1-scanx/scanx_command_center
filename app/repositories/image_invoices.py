from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.image_invoices import CCImageInvoice

# Statuses that count as "an invoice is out there for this appointment". They
# match the partial unique index on cc_image_invoices.
ACTIVE_STATUSES = ("pending", "sent", "paid")


def create_image_invoice(
    db: Session,
    *,
    appointment_id: str,
    customer_email: str,
    amount_cents: int,
    currency: str,
    sent_by_user_id: int,
) -> CCImageInvoice:
    invoice = CCImageInvoice(
        appointment_id=appointment_id,
        customer_email=customer_email,
        amount_cents=amount_cents,
        currency=currency,
        status="pending",
        sent_by_user_id=sent_by_user_id,
    )

    db.add(invoice)
    db.flush()

    return invoice


def expire_stale_pending_image_invoices(
    db: Session,
    *,
    appointment_id: str,
    created_before: datetime,
) -> None:
    """A request that died halfway (process killed, deploy) would leave its
    row `pending` forever and block the appointment's unique slot. Rows older
    than the cutoff are closed out as failed.
    """
    db.execute(
        update(CCImageInvoice)
        .where(
            CCImageInvoice.appointment_id == appointment_id,
            CCImageInvoice.status == "pending",
            CCImageInvoice.created_at < created_before,
        )
        .values(
            status="failed",
            error_message="Abandoned: the request never finished.",
        )
    )
    db.flush()


def get_active_image_invoice(
    db: Session,
    appointment_id: str,
) -> CCImageInvoice | None:
    return db.scalar(
        select(CCImageInvoice)
        .where(
            CCImageInvoice.appointment_id == appointment_id,
            CCImageInvoice.status.in_(ACTIVE_STATUSES),
        )
        .order_by(CCImageInvoice.id.desc())
        .limit(1)
    )


def get_latest_image_invoice(
    db: Session,
    appointment_id: str,
) -> CCImageInvoice | None:
    return db.scalar(
        select(CCImageInvoice)
        .where(CCImageInvoice.appointment_id == appointment_id)
        .order_by(CCImageInvoice.id.desc())
        .limit(1)
    )


def get_image_invoice_by_stripe_invoice_id(
    db: Session,
    stripe_invoice_id: str,
) -> CCImageInvoice | None:
    return db.scalar(
        select(CCImageInvoice)
        .where(CCImageInvoice.stripe_invoice_id == stripe_invoice_id)
        .order_by(CCImageInvoice.id.desc())
        .limit(1)
    )
