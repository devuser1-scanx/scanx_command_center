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


class CCMailTransmission(Base):
    """One row per file included in a mail send attempt.

    Files attached to the same "Send" click share to/cc/bcc addresses,
    subject, status and gmail_message_id, since Gmail sends every
    attachment in a single message as one email.
    """

    __tablename__ = "cc_mail_transmissions"

    __table_args__ = (
        Index(
            "ix_cc_mail_transmissions_appointment_id",
            "appointment_id",
        ),
        Index(
            "ix_cc_mail_transmissions_sent_by_user_id",
            "sent_by_user_id",
        ),
        Index(
            "ix_cc_mail_transmissions_gmail_message_id",
            "gmail_message_id",
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

    to_addresses: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    cc_addresses: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    bcc_addresses: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    gmail_message_id: Mapped[str | None] = mapped_column(
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
            name="fk_cc_mail_transmissions_sent_by_user_id",
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
