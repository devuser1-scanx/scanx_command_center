from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CCImageInvoice(Base):
    """One row per "Send Image Invoice" attempt: the fixed $45 Stripe invoice
    a patient is sent for their ultrasound exam images.

    Separate from the referral-organization invoicing in the production
    database (organization_invoices); nothing here touches those tables.

    status: pending (being created) | sent | paid | failed | void |
    uncollectible. A partial unique index allows only one pending/sent/paid
    invoice per appointment, so two simultaneous clicks cannot bill twice;
    failed and void rows do not block a new one.
    """

    __tablename__ = "cc_image_invoices"

    __table_args__ = (
        Index(
            "ix_cc_image_invoices_appointment_id",
            "appointment_id",
        ),
        Index(
            "ix_cc_image_invoices_stripe_invoice_id",
            "stripe_invoice_id",
        ),
        Index(
            "ix_cc_image_invoices_sent_by_user_id",
            "sent_by_user_id",
        ),
        Index(
            "uq_cc_image_invoices_active_appointment",
            "appointment_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'sent', 'paid')"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    appointment_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    customer_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    stripe_invoice_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    hosted_invoice_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    amount_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sent_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_image_invoices_sent_by_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_stripe_event_id: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
