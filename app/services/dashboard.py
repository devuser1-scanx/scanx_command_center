from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.time_utils import FALLBACK_TIMEZONE
from app.models.production import Appointment
from app.repositories.dashboard import (
    list_appointments_for_day,
    list_clinics,
    list_reported_appointment_ids,
)
from app.schemas.dashboard import (
    DashboardSummaryResponse,
    DashboardTimelineResponse,
    TimelineAppointmentResponse,
    Tone,
)

DEFAULT_DURATION_MINUTES = 30


def derive_status_and_tone(appointment: Appointment) -> tuple[str, Tone]:
    """
    `status_label` on the production `appointment` row is not always kept in
    sync with the boolean flags (observed in real data: rows with
    status_label='Confirmed' but confirmed=False, and rows with
    status_label=NULL but checkin=True). This precedence order is a
    best-effort interpretation of that data, not an authored spec - it
    should be validated against what clinic staff actually expect to see.
    """
    status_label = (appointment.status_label or "").strip()

    if appointment.canceled:
        return "Cancelled", "red"

    if status_label == "No Show":
        return "No Show", "red"

    if status_label in ("Completed", "Complete"):
        return "Completed", "green"

    if appointment.checkin:
        return "Checked In", "orange"

    if appointment.confirmed or status_label == "Confirmed":
        return "Confirmed", "blue"

    return "Scheduled", "purple"


def build_patient_name(appointment: Appointment) -> str:
    name = f"{appointment.first_name or ''} {appointment.last_name or ''}".strip()
    return name or "Unknown Patient"


def build_local_time(appointment: Appointment, clinic_timezone: str | None) -> str:
    if appointment.appointment_datetime is None:
        return appointment.time or ""

    tz = ZoneInfo(clinic_timezone or FALLBACK_TIMEZONE)
    local_dt = appointment.appointment_datetime.astimezone(tz)

    return local_dt.strftime("%H:%M")


def to_timeline_appointment(
    appointment: Appointment,
    *,
    clinic_timezone: str | None,
) -> TimelineAppointmentResponse:
    status, tone = derive_status_and_tone(appointment)

    return TimelineAppointmentResponse(
        id=appointment.appointment_id,
        patient=build_patient_name(appointment),
        exam=appointment.appointment_type or appointment.category or "Exam",
        time=build_local_time(appointment, clinic_timezone),
        status=status,
        tone=tone,
        duration_minutes=appointment.duration or DEFAULT_DURATION_MINUTES,
    )


def get_dashboard_timeline(
    prod_db: Session,
    *,
    clinic_id: int | None,
    day: date,
) -> DashboardTimelineResponse:
    clinic_timezones = {clinic.id: clinic.timezone for clinic in list_clinics(prod_db)}

    appointments = list_appointments_for_day(prod_db, clinic_id=clinic_id, day=day)

    return DashboardTimelineResponse(
        clinic_id=clinic_id,
        date=day,
        appointments=[
            to_timeline_appointment(
                appointment,
                clinic_timezone=clinic_timezones.get(appointment.clinic_id),
            )
            for appointment in appointments
        ],
    )


def is_late(appointment: Appointment, *, now_utc: datetime) -> bool:
    """
    Best-effort definition (there's no "late" flag in the source data):
    scheduled for earlier today, not canceled, and no sign yet that they
    arrived or the visit otherwise concluded (no check-in, not completed,
    not marked no-show). Flagging this the same way derive_status_and_tone
    is flagged - worth validating against what staff actually mean by
    "late" once this is in front of them.
    """
    if appointment.canceled:
        return False

    if appointment.checkin:
        return False

    status_label = (appointment.status_label or "").strip()

    if status_label in ("Completed", "Complete", "No Show"):
        return False

    if appointment.appointment_datetime is None:
        return False

    return appointment.appointment_datetime < now_utc


def get_dashboard_summary(
    prod_db: Session,
    *,
    day: date,
) -> DashboardSummaryResponse:
    appointments = list_appointments_for_day(prod_db, clinic_id=None, day=day)
    now_utc = datetime.now(timezone.utc)

    confirmed = 0
    checked_in = 0
    late = 0
    completed_ids: list[str] = []

    for appointment in appointments:
        status, _tone = derive_status_and_tone(appointment)

        if status == "Confirmed":
            confirmed += 1
        elif status == "Checked In":
            checked_in += 1
        elif status == "Completed":
            completed_ids.append(appointment.appointment_id)

        if is_late(appointment, now_utc=now_utc):
            late += 1

    reported_ids = list_reported_appointment_ids(prod_db, completed_ids)
    report_pending = sum(
        1 for appointment_id in completed_ids if appointment_id not in reported_ids
    )

    return DashboardSummaryResponse(
        date=day,
        today=len(appointments),
        confirmed=confirmed,
        checked_in=checked_in,
        late=late,
        report_pending=report_pending,
    )
