from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time_utils import FALLBACK_TIMEZONE, UTC, day_bounds_utc
from app.models.production import Appointment, Clinic, Report


def list_clinics(prod_db: Session) -> list[Clinic]:
    statement = select(Clinic).order_by(Clinic.id)
    return list(prod_db.scalars(statement).all())


def _appointments_between(
    prod_db: Session,
    *,
    clinic_ids: list[int],
    start_utc: datetime,
    end_utc: datetime,
) -> list[Appointment]:
    statement = (
        select(Appointment)
        .where(
            Appointment.clinic_id.in_(clinic_ids),
            Appointment.appointment_datetime >= start_utc,
            Appointment.appointment_datetime <= end_utc,
        )
        .order_by(Appointment.appointment_datetime)
    )

    return list(prod_db.scalars(statement).all())


def list_appointments_for_day(
    prod_db: Session,
    *,
    clinic_id: int | None,
    day: date,
) -> list[Appointment]:
    """
    `appointment_datetime` is stored in UTC, so the day boundary must be
    computed in the clinic's own local timezone first (via `clinics.timezone`)
    and then converted to UTC - a naive UTC-day query would clip early
    morning / late evening local appointments.
    """
    if clinic_id is not None:
        clinic = prod_db.get(Clinic, clinic_id)
        tz_name = clinic.timezone if clinic else None
        start_utc, end_utc = day_bounds_utc(day, tz_name)

        return _appointments_between(
            prod_db,
            clinic_ids=[clinic_id],
            start_utc=start_utc,
            end_utc=end_utc,
        )

    # "All clinics": group by timezone so each group's day boundary is
    # computed correctly, then merge back together in chronological order.
    clinics_by_timezone: dict[str, list[int]] = {}

    for clinic in list_clinics(prod_db):
        tz_name = clinic.timezone or FALLBACK_TIMEZONE
        clinics_by_timezone.setdefault(tz_name, []).append(clinic.id)

    appointments: list[Appointment] = []

    for tz_name, clinic_ids in clinics_by_timezone.items():
        start_utc, end_utc = day_bounds_utc(day, tz_name)
        appointments.extend(
            _appointments_between(
                prod_db,
                clinic_ids=clinic_ids,
                start_utc=start_utc,
                end_utc=end_utc,
            )
        )

    appointments.sort(
        key=lambda appt: appt.appointment_datetime or datetime.min.replace(tzinfo=UTC)
    )

    return appointments


def list_reported_appointment_ids(
    prod_db: Session,
    appointment_ids: list[str],
) -> set[str]:
    """Which of these appointment_ids have at least one row in `reports`."""
    if not appointment_ids:
        return set()

    statement = select(Report.appointment_id).where(Report.appointment_id.in_(appointment_ids))

    return {
        appointment_id
        for appointment_id in prod_db.scalars(statement).all()
        if appointment_id is not None
    }
