from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CCGoogleReview(Base):
    """One Google review link per clinic, used to prefill the "Ask For
    Review" text message.

    clinic_id is the production `clinics` table's primary key, stored as a
    plain column rather than a real foreign key - `clinics` lives in a
    separate, read-only production database that Command Center's own
    tables can't reference across (see app/models/production.py).
    """

    __tablename__ = "cc_google_reviews"

    __table_args__ = (
        Index(
            "ix_cc_google_reviews_clinic_id",
            "clinic_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    clinic_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    google_review_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
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
