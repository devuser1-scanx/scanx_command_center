from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CCSmsTransmission(Base):
    """One row per text message send attempt.

    purpose identifies which button triggered the send (e.g.
    "ask_for_review", "directions") - free-form rather than an enum since
    more buttons will use this same table later (Reschedule, Report).
    """

    __tablename__ = "cc_sms_transmissions"

    __table_args__ = (
        Index(
            "ix_cc_sms_transmissions_appointment_id",
            "appointment_id",
        ),
        Index(
            "ix_cc_sms_transmissions_sent_by_user_id",
            "sent_by_user_id",
        ),
        Index(
            "ix_cc_sms_transmissions_twilio_message_sid",
            "twilio_message_sid",
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

    purpose: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    destination_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    twilio_message_sid: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sent_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_sms_transmissions_sent_by_user_id",
            ondelete="SET NULL",
        ),
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
