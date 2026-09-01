from __future__ import annotations

import re
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.production import (
    Appointment,
    CallLog,
    Checkin,
    Clinic,
    FormStatus,
    FormTracking,
    Message,
    Patient,
    Report,
    Upload,
)
from app.repositories.dashboard import list_appointments_for_day

SEARCH_RESULT_LIMIT = 200


def search_patients(
    prod_db: Session,
    *,
    query: str | None,
    day: date | None,
) -> list[Appointment]:
    """
    With no query, falls back to the given day's appointments across all
    clinics (reusing the dashboard repository's clinic-timezone-aware day
    boundary logic). With a query, matches appointment_id, name (first,
    last, or "first last"), or phone (compared digit-only so formatting
    like "(555) 222-0198" vs "5552220198" doesn't matter) - no date
    restriction, since a patient's past visit should still be findable.
    """
    term = (query or "").strip()

    if not term:
        if day is None:
            return []

        return list_appointments_for_day(prod_db, clinic_id=None, day=day)

    digits = re.sub(r"\D", "", term)

    conditions = [
        Appointment.appointment_id.ilike(f"%{term}%"),
        func.concat(Appointment.first_name, " ", Appointment.last_name).ilike(f"%{term}%"),
    ]

    if len(digits) >= 4:
        conditions.append(
            func.regexp_replace(Appointment.phone, r"\D", "", "g").ilike(f"%{digits}%")
        )

    statement = (
        select(Appointment)
        .where(or_(*conditions))
        .order_by(Appointment.appointment_datetime.desc())
        .limit(SEARCH_RESULT_LIMIT)
    )

    return list(prod_db.scalars(statement).all())


def list_appointments_for_phone(
    prod_db: Session,
    phone: str | None,
) -> list[Appointment]:
    if not phone:
        return []

    statement = (
        select(Appointment)
        .where(Appointment.phone == phone)
        .order_by(Appointment.appointment_datetime.desc())
    )

    return list(prod_db.scalars(statement).all())


def get_appointment_by_appointment_id(
    prod_db: Session,
    appointment_id: str,
) -> Appointment | None:
    statement = select(Appointment).where(Appointment.appointment_id == appointment_id)

    return prod_db.scalar(statement)


def get_clinic(prod_db: Session, clinic_id: int) -> Clinic | None:
    statement = select(Clinic).where(Clinic.id == clinic_id)
    return prod_db.scalar(statement)


def get_patient_intake(prod_db: Session, appointment_id: str) -> Patient | None:
    statement = select(Patient).where(Patient.appointment_id == appointment_id)
    return prod_db.scalar(statement)


def get_checkin(prod_db: Session, appointment_id: str) -> Checkin | None:
    statement = select(Checkin).where(Checkin.appointment_id == appointment_id)
    return prod_db.scalar(statement)


def list_form_status(prod_db: Session, appointment_id: str) -> list[FormStatus]:
    statement = select(FormStatus).where(FormStatus.appointment_id == appointment_id)
    return list(prod_db.scalars(statement).all())


def list_form_tracking(prod_db: Session, appointment_id: str) -> list[FormTracking]:
    statement = select(FormTracking).where(FormTracking.appointment_id == appointment_id)
    return list(prod_db.scalars(statement).all())


def list_messages(prod_db: Session, appointment_id: str) -> list[Message]:
    statement = (
        select(Message).where(Message.appointment_id == appointment_id).order_by(Message.timestamp)
    )
    return list(prod_db.scalars(statement).all())


def list_call_logs(prod_db: Session, appointment_id: str) -> list[CallLog]:
    statement = (
        select(CallLog).where(CallLog.appointment_id == appointment_id).order_by(CallLog.created_at)
    )
    return list(prod_db.scalars(statement).all())


def list_reports(prod_db: Session, appointment_id: str) -> list[Report]:
    statement = (
        select(Report).where(Report.appointment_id == appointment_id).order_by(Report.created_at)
    )
    return list(prod_db.scalars(statement).all())


def list_uploads(prod_db: Session, appointment_id: str) -> list[Upload]:
    statement = (
        select(Upload).where(Upload.appointment_id == appointment_id).order_by(Upload.uploaded_at)
    )
    return list(prod_db.scalars(statement).all())
