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


class CCFaxTransmission(Base):
    """One row per file included in a fax send attempt.

    Files attached to the same "Send" click share destination_number,
    status and westfax_job_id, since WestFax combines every file in a
    single SendFax call into one physical fax document sent as one job.
    """

    __tablename__ = "cc_fax_transmissions"

    __table_args__ = (
        Index(
            "ix_cc_fax_transmissions_appointment_id",
            "appointment_id",
        ),
        Index(
            "ix_cc_fax_transmissions_sent_by_user_id",
            "sent_by_user_id",
        ),
        Index(
            "ix_cc_fax_transmissions_westfax_job_id",
            "westfax_job_id",
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

    destination_number: Mapped[str] = mapped_column(
        String(32),
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

    westfax_job_id: Mapped[str | None] = mapped_column(
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
            name="fk_cc_fax_transmissions_sent_by_user_id",
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
