from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.acuity_client import (
    AcuityApiError,
    create_appointment,
    list_availability_dates,
    list_availability_times,
)
from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpAcuityCalendar,
    CCFpReferralBoard,
    CCFpReferralExam,
    CCFpStatus,
)
from app.schemas.fax_portal import AvailableTime, DirectBookResponse
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_booking import (
    _forms_by_exam,
    _phone_for_acuity,
    intake_field_values,
)
from app.services.fax_portal_referrals import (
    BOOKED,
    REVIEW,
    InvalidMoveError,
    MoveExtra,
    ReferralError,
    ReferralNotFoundError,
    move_referral,
)

# Books the appointment in Acuity itself (POST /appointments) instead of sending
# the agent to the public scheduling page. The API has no payment step, so the
# appointment is created unpaid and payment is collected at check-in.
#
# Never log names, phone numbers, emails or intake answers here: only ids.
logger = logging.getLogger(__name__)

R = CCFpReferralBoard

DEFAULT_TIMEZONE = "America/Chicago"

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Acuity's answers to "that did not work", as something an agent can act on.
_TAKEN_CODES = {
    "not_available",
    "not_available_min_hours_in_advance",
    "not_available_max_days_in_advance",
    "no_available_calendar",
}


class DirectBookingError(ReferralError):
    """The booking cannot be made as things stand; the message says what to fix."""

    status_code = 422


class TimeTakenError(ReferralError):
    status_code = 409


class AcuityUnavailableError(ReferralError):
    status_code = 502


def _unavailable(exc: AcuityApiError) -> AcuityUnavailableError:
    logger.warning("Acuity request failed (code %s).", exc.code)

    return AcuityUnavailableError("Acuity could not be reached. Please try again in a moment.")


def _clean(value: str | None) -> str | None:
    stripped = (value or "").strip()

    return stripped or None


class _Context:
    def __init__(self, card: R, exam_id: int, calendar_id: int, timezone: str) -> None:
        self.card = card
        self.exam_id = exam_id
        self.calendar_id = calendar_id
        self.timezone = timezone


def _context(db: Session, card: R) -> _Context:
    """The exam and clinic a direct booking needs, or why it cannot be made."""
    if card.status == REVIEW:
        raise DirectBookingError("Release this referral from Intake Review before booking.")

    status_row = db.get(CCFpStatus, card.status)

    if status_row is not None and not status_row.is_open:
        raise DirectBookingError("This referral is closed.")

    exam_id = db.scalar(
        select(CCFpReferralExam.acuity_appointment_type_id).where(
            CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == 1
        )
    )

    if exam_id is None or card.acuity_calendar_id is None:
        raise DirectBookingError("Choose Exam 1 and the clinic in the booking details first.")

    calendar = db.get(CCFpAcuityCalendar, card.acuity_calendar_id)
    timezone = (calendar.timezone if calendar else None) or DEFAULT_TIMEZONE

    return _Context(card, int(exam_id), int(card.acuity_calendar_id), timezone)


def _load(db: Session, referral_id: int, *, lock: bool = False) -> R:
    query = select(R).where(R.id == referral_id)

    if lock:
        query = query.with_for_update()

    card = db.scalar(query)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    return card


def _local(value: str, timezone: str) -> datetime | None:
    """Acuity's "2026-10-20T10:00:00-0500" as a datetime in the clinic's timezone."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").astimezone(ZoneInfo(timezone))
    except (ValueError, KeyError):
        return None


def _label(when: datetime) -> str:
    hour = when.hour % 12 or 12

    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


# ---------------------------------------------------------------------
# Open dates and times
# ---------------------------------------------------------------------


def availability_dates(db: Session, referral_id: int, month: str) -> list[str]:
    """Days in `month` (YYYY-MM) with open times for the referral's exam and clinic."""
    if not _MONTH.match(month):
        raise DirectBookingError("Pick a month.")

    context = _context(db, _load(db, referral_id))

    try:
        items = list_availability_dates(
            appointment_type_id=context.exam_id, calendar_id=context.calendar_id, month=month
        )
    except AcuityApiError as exc:
        raise _unavailable(exc) from exc
    except RuntimeError as exc:
        raise AcuityUnavailableError("Acuity is not set up.") from exc

    return sorted(str(item["date"]) for item in items if item.get("date"))


def availability_times(db: Session, referral_id: int, day: str) -> list[AvailableTime]:
    """Open times on `day` (YYYY-MM-DD), with the time of day in the clinic's timezone."""
    if not _DAY.match(day):
        raise DirectBookingError("Pick a date.")

    context = _context(db, _load(db, referral_id))

    return _times(context, day)


def _times(context: _Context, day: str) -> list[AvailableTime]:
    try:
        items = list_availability_times(
            appointment_type_id=context.exam_id, calendar_id=context.calendar_id, date=day
        )
    except AcuityApiError as exc:
        raise _unavailable(exc) from exc
    except RuntimeError as exc:
        raise AcuityUnavailableError("Acuity is not set up.") from exc

    slots: list[AvailableTime] = []

    for item in items:
        raw = str(item.get("time") or "")
        local = _local(raw, context.timezone)

        if local is not None:
            slots.append(AvailableTime(time=raw, label=_label(local)))

    return slots


# ---------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------


def _intake_answers(card: R, exam_id: int) -> list[dict[str, Any]]:
    """The intake form's answers for Acuity: the referral's details, and "yes" for
    every required checkbox (the consent statements).

    The consents are sent as "yes" automatically, not confirmed with the patient
    one by one: the audit entry for the booking says so. Raises
    DirectBookingError naming any required question that cannot be answered."""
    fields = _forms_by_exam().get(exam_id, [])
    values = dict(intake_field_values(card, fields))
    answers: list[dict[str, Any]] = []
    missing: list[str] = []

    for field in fields:
        field_id = field.get("id")
        key = f"field:{field_id}"
        name = str(field.get("name") or "A required question")[:80]

        if key in values:
            answers.append({"id": field_id, "value": values[key]})
        elif field.get("type") == "checkbox" and field.get("required"):
            answers.append({"id": field_id, "value": "yes"})
        elif field.get("required") and field.get("type") in ("dropdown", "textbox", "textarea"):
            missing.append(name)

    if missing:
        raise DirectBookingError(
            "Acuity needs these in the booking details first: " + "; ".join(missing)
        )

    return answers


def book(
    db: Session,
    referral_id: int,
    *,
    time: str,
    email: str | None,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DirectBookResponse:
    """Books the referral's exam at its clinic at `time` (one of the open times
    from availability_times), then marks the referral Appointment Booked with the
    new Acuity appointment.

    The referral row is locked, so two tabs cannot book it twice. The time is
    checked against Acuity's open times again here, so nothing but a real open
    slot can be booked."""
    card = _load(db, referral_id, lock=True)
    context = _context(db, card)

    if not card.first_name or not card.last_name:
        raise DirectBookingError("Add the patient's first and last name first.")

    day = time[:10]

    if not _DAY.match(day):
        raise DirectBookingError("Pick a time.")

    slot = next((s for s in _times(context, day) if s.time == time), None)

    if slot is None:
        raise TimeTakenError("That time is no longer available. Pick another.")

    answers = _intake_answers(card, context.exam_id)
    patient_email = _clean(email) or _clean(card.email)

    body: dict[str, Any] = {
        "datetime": time,
        "appointmentTypeID": context.exam_id,
        "calendarID": context.calendar_id,
        "firstName": card.first_name,
        "lastName": card.last_name,
        "fields": answers,
    }
    phone = _phone_for_acuity(card)

    if phone:
        body["phone"] = phone

    if patient_email:
        body["email"] = patient_email

    # Acuity requires an email unless booking as an admin. Admin mode skips its
    # own availability checks, which is why the open time was confirmed above.
    admin = patient_email is None

    try:
        appointment = create_appointment(body, admin=admin)
    except AcuityApiError as exc:
        if exc.code in _TAKEN_CODES:
            raise TimeTakenError("That time was just taken. Pick another.") from exc

        if exc.code in {"required_field", "invalid_fields", "required_email"}:
            raise DirectBookingError(f"Acuity needs more details: {exc.message}") from exc

        if exc.code is None:
            raise _unavailable(exc) from exc

        raise DirectBookingError(f"Acuity could not book the appointment: {exc.message}") from exc
    except RuntimeError as exc:
        raise AcuityUnavailableError("Acuity is not set up.") from exc

    appointment_id = str(appointment["id"])

    if patient_email and patient_email != card.email:
        card.email = patient_email

    try:
        moved = move_referral(
            db,
            card.id,
            BOOKED,
            source="button",
            user=user,
            extra=MoveExtra(acuity_appointment_id=appointment_id),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except InvalidMoveError as exc:
        # The appointment exists in Acuity already: say so, so it can be linked.
        raise DirectBookingError(
            f"Booked in Acuity as appointment {appointment_id}, but the referral could "
            f"not be updated: {exc}"
        ) from exc

    log_activity(
        db,
        section="referral_case",
        action="direct_book",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        details={
            "appointment_id": appointment_id,
            # No payment is taken by the API: collected at check-in.
            "paid": False,
            # How the intake's required consent checkboxes were answered.
            "consents": "sent_as_yes_automatically",
            "admin_mode": admin,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    local = _local(time, context.timezone)
    when_label = f"{local:%b} {local.day} at {_label(local)}" if local else time

    return DirectBookResponse(
        appointment_id=appointment_id,
        status=moved.to_status,
        row_version=moved.referral.row_version,
        when_label=when_label,
    )
