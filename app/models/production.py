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
Read-only models mapped onto tables that already exist in the production
ScanX database. Only the columns this feature actually reads are mapped -
the real tables have more columns than are declared here, which is fine for
read-only access and keeps this file scoped to what Command Center uses.

These models are NEVER created/altered/dropped by Command Center. They are
mapped against `ProdBase`, a declarative base Alembic's migrations never see
(see app/db/prod_base.py), and every query against them must be SELECT-only.
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
    token_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    canceled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    prep_ack: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    clinic_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    appointment_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    checkin_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)


class FormStatus(ProdBase):
    __tablename__ = "form_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(String, nullable=False)
    patient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_status: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class FormTracking(ProdBase):
    __tablename__ = "form_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str] = mapped_column(Text, nullable=False)
    form_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(ProdBase):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class CallLog(ProdBase):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class Report(ProdBase):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    accessed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class Upload(ProdBase):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
