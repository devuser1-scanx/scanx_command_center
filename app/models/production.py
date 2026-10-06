from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.prod_base import ProdBase

"""
Models mapped onto tables that already exist in the production ScanX database.

Only the columns Command Center currently needs are mapped. The real production
tables may contain additional columns, which is valid because Command Center
does not manage the production schema.

These models are NEVER created, altered, or dropped by Command Center.
They are mapped against ProdBase, a declarative base that Command Center's
Alembic migrations do not manage (see app/db/prod_base.py).

Production access is read-only by default. A very small number of explicitly
approved write operations are isolated in app/repositories/production_writes.py:

1. Record outbound SMS messages in the existing `messages` table.
2. Record sent forms in the existing `form_tracking` table.
3. Record a Command Center manual patient check-in by updating the existing
   `appointment` row and upserting the existing `checkins` row.

No other production writes should be added outside that repository without
an explicit architectural decision.
"""


class Clinic(ProdBase):
    __tablename__ = "clinics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    map_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Appointment(ProdBase):
    __tablename__ = "appointment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(String(50), nullable=False)

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)

    appointment_type: Mapped[str | None] = mapped_column(String(150), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)

    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    time: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    amount_paid: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    paid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payment_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    status_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    checkin: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    token_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    canceled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    prep_ack: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    clinic_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    appointment_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )


class Patient(ProdBase):
    """Intake/demographic details for a single appointment (1:1 via appointment_id)."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(String(50), nullable=False)

    dob: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    physician_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    physician_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    physician_fax_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    doctor_order_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    blood_pressure_sys: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blood_pressure_dia: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sms_consent_value: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hippa_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    accept_terms: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Checkin(ProdBase):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(String(50), nullable=False)

    checkin_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)


class FormStatus(ProdBase):
    __tablename__ = "form_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(String, nullable=False)
    patient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_status: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )


class FormTracking(ProdBase):
    __tablename__ = "form_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(Text, nullable=False)
    patient_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    dob: Mapped[str | None] = mapped_column(Text, nullable=True)
    appointment_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    appointment_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Message(ProdBase):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recipient: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )


class CallLog(ProdBase):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )


class Report(ProdBase):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    accessed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )


class Upload(ProdBase):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
