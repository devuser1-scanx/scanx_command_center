from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Command Center's own record of every call placed from the Calls module (the
# Web Dialer). The production database's `call_logs` table - the one the
# standalone Web Dialer service also writes - holds the shared record; this
# table adds who placed the call, which production has no column for.
# `prod_call_log_id` points at call_logs.id (a plain column: no foreign key
# across databases).


class CCCallLog(Base):
    __tablename__ = "cc_call_logs"

    __table_args__ = (
        CheckConstraint("direction IN ('outbound','inbound')"),
        Index("ix_cc_call_logs_user", "user_id", "started_at"),
        Index("ix_cc_call_logs_prod_call_log_id", "prod_call_log_id"),
        Index(
            "uq_cc_call_logs_twilio_call_sid",
            "twilio_call_sid",
            unique=True,
            postgresql_where=text("twilio_call_sid IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_users.id", ondelete="SET NULL")
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False, server_default="outbound")
    # The number dialled, in E.164. Set by the server, never taken from Twilio's
    # request, so a browser cannot change who is called.
    to_number: Mapped[str] = mapped_column(String(32), nullable=False)
    appointment_id: Mapped[str | None] = mapped_column(String(50))
    twilio_call_sid: Mapped[str | None] = mapped_column(String(100))
    # queued -> initiated -> ringing -> in-progress -> completed | busy | no-answer
    # | failed | canceled; or "abandoned" when the browser never started the call.
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="queued")
    duration_s: Mapped[int | None] = mapped_column(Integer)
    prod_call_log_id: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
