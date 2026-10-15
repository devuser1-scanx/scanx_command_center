from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.acuity_client import AcuityApiError, list_forms
from app.models.fax_portal import (
    CCFpAcuityAppointmentType,
    CCFpReferralBoard,
    CCFpReferralExam,
)
from app.models.production import Appointment
from app.schemas.fax_portal import BookingMatch

R = CCFpReferralBoard

MAX_MATCHES = 10


# Acuity's intake-form fields (reason, gender, date of birth, physician) are
# prefilled with `field:<id>=<value>`; the ids come from GET /forms and are
# cached, as they only change when someone edits a form in Acuity.
FORMS_CACHE_SECONDS = 3600
FORMS_FAILURE_CACHE_SECONDS = 60

_forms_cache: tuple[float, dict[int, list[dict[str, Any]]]] | None = None


def _forms_by_exam() -> dict[int, list[dict[str, Any]]]:
    """Intake-form fields of every appointment type: {appointment type id:
    fields}. Empty when Acuity cannot be reached (the link is then built
    without them)."""
    global _forms_cache

    now = time.monotonic()

    if _forms_cache is not None and _forms_cache[0] > now:
        return _forms_cache[1]

    by_exam: dict[int, list[dict[str, Any]]] = {}

    try:
        for form in list_forms():
            for exam_id in form.get("appointmentTypeIDs") or []:
                by_exam[int(exam_id)] = [f for f in form.get("fields") or [] if isinstance(f, dict)]

        ttl = FORMS_CACHE_SECONDS
    except (AcuityApiError, RuntimeError, ValueError, TypeError):
        ttl = FORMS_FAILURE_CACHE_SECONDS

    _forms_cache = (now + ttl, by_exam)

    return by_exam


def _field_kind(name: str) -> str | None:
    """Which referral value an Acuity intake-form field takes, from its label.
    Consent checkboxes and the file upload are never filled: the patient does
    those."""
    n = name.lower()

    if "other" in n and "explain" in n:
        return "reason_other"

    if "reason" in n and "appointment" in n:
        return "reason"

    if n.startswith("gender"):
        return "gender"

    if "date of birth" in n or "dob" in n:
        return "dob"

    if "physician" in n and "fax" in n:
        return "physician_fax"

    if "physician" in n and "email" in n:
        return "physician_email"

    if "physician" in n or n.startswith("family"):
        return "physician"

    return None


def _option(value: str | None, options: list[str]) -> str | None:
    """The dropdown option Acuity needs for `value`: its own spelling of it."""
    wanted = (value or "").strip().casefold()

    if not wanted:
        return None

    return next((o for o in options if o.strip().casefold() == wanted), None)


def _gender_option(value: str | None, options: list[str]) -> str | None:
    exact = _option(value, options)

    if exact or not value:
        return exact

    # The fax often says just "F" or "M".
    return _option({"f": "Female", "m": "Male"}.get(value.strip().casefold()), options)


def intake_field_values(card: R, fields: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """`field:<id>` parameters that prefill the booking form's intake questions
    from the referral. Dropdowns only get a value Acuity lists; a reason that
    is not on the list goes in as "Other" plus the explanation."""
    values: list[tuple[str, str]] = []
    chosen_reason: str | None = None

    for field in fields:
        if _field_kind(str(field.get("name") or "")) == "reason":
            options = [str(o) for o in field.get("options") or []]
            chosen_reason = _option(card.reason, options)

            if chosen_reason is None and (card.reason or card.reason_other):
                chosen_reason = _option("Other", options)

    for field in fields:
        if field.get("type") not in ("dropdown", "textbox", "textarea"):
            continue

        kind = _field_kind(str(field.get("name") or ""))
        options = [str(o) for o in field.get("options") or []]
        value: str | None = None

        if kind == "reason":
            value = chosen_reason
        elif kind == "reason_other":
            # Only when the reason ended up as "Other".
            if (chosen_reason or "").casefold() == "other":
                value = card.reason_other or card.reason
        elif kind == "gender":
            value = _gender_option(card.gender, options)
        elif kind == "dob":
            value = card.dob.strftime("%m/%d/%Y") if card.dob else None
        elif kind == "physician":
            value = card.physician_name
        elif kind == "physician_fax":
            digits = "".join(ch for ch in (card.physician_fax_no or "") if ch.isdigit())
            value = digits[-10:] if len(digits) >= 10 else None
        elif kind == "physician_email":
            value = card.physician_email

        if value and value.strip() and field.get("id") is not None:
            values.append((f"field:{field['id']}", value.strip()))

    return values


def build_booking_link(
    db: Session, card: R, *, include_intake_fields: bool = False
) -> tuple[str | None, str | None]:
    """The Acuity scheduling page for the referral's exam and clinic, with the
    patient's details prefilled (Acuity's query-string prefill).

    Returns (link, None), or (None, why it cannot be built). Needs Exam 1 and
    its scheduling URL from the synced Acuity data. The clinic is added when
    chosen; otherwise the patient picks it on the page.

    `include_intake_fields` also fills the intake questions (reason, gender,
    date of birth, physician): for the agent booking on the patient's behalf,
    not for the link texted to the patient, which carries only name, phone and
    email.
    """
    exam_id = db.scalar(
        select(CCFpReferralExam.acuity_appointment_type_id).where(
            CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == 1
        )
    )

    if exam_id is None:
        return None, "Choose Exam 1 in the booking details first."

    base = db.scalar(
        select(CCFpAcuityAppointmentType.scheduling_url).where(
            CCFpAcuityAppointmentType.acuity_appointment_type_id == exam_id
        )
    )

    if not base:
        return None, "Acuity has no booking page for this exam. Refresh from Acuity and try again."

    intake = (
        intake_field_values(card, _forms_by_exam().get(int(exam_id), []))
        if include_intake_fields
        else []
    )

    return prefill_booking_url(base, card, intake), None


def _phone_for_acuity(card: R) -> str | None:
    """US numbers as (214) 555-0187, the form Acuity's phone box reads; stored
    numbers are +1XXXXXXXXXX."""
    digits = "".join(ch for ch in (card.phone_e164 or card.phone or "") if ch.isdigit())

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

    return card.phone


def prefill_booking_url(
    base_url: str, card: R, extra_params: list[tuple[str, str]] | None = None
) -> str:
    """Adds calendarID and the patient's details to an Acuity scheduling URL,
    keeping whatever the URL already carries (owner, appointmentType, ...)."""
    parts = urlsplit(base_url)
    params = parse_qsl(parts.query, keep_blank_values=True)

    extra = {
        "calendarID": str(card.acuity_calendar_id) if card.acuity_calendar_id else None,
        "firstName": card.first_name,
        "lastName": card.last_name,
        "phone": _phone_for_acuity(card),
        "email": card.email,
    }
    existing = {key for key, _ in params}

    for key, value in extra.items():
        if value and key not in existing:
            params.append((key, value))

    for key, value in extra_params or []:
        if key not in existing:
            params.append((key, value))

    return urlunsplit(parts._replace(query=urlencode(params)))


def find_appointment_matches(prod_db: Session, db: Session, card: R) -> list[BookingMatch]:
    """Appointments in the production `appointment` table that look like this
    patient's booking: same phone number, or same first and last name. Read
    only. Recent ones first; cancelled ones are listed but flagged."""
    phone_digits = "".join(ch for ch in (card.phone_e164 or card.phone or "") if ch.isdigit())[-10:]
    first = (card.first_name or "").strip().lower()
    last = (card.last_name or "").strip().lower()

    conditions = []

    if len(phone_digits) == 10:
        conditions.append(
            func.right(func.regexp_replace(Appointment.phone, "[^0-9]", "", "g"), 10)
            == phone_digits
        )

    if first and last:
        conditions.append(
            (func.lower(Appointment.first_name) == first)
            & (func.lower(Appointment.last_name) == last)
        )

    if not conditions:
        return []

    combined = conditions[0]

    for condition in conditions[1:]:
        combined = combined | condition

    rows = prod_db.scalars(
        select(Appointment)
        .where(combined)
        .order_by(Appointment.appointment_datetime.desc().nulls_last(), Appointment.id.desc())
        .limit(MAX_MATCHES)
    ).all()

    ids = [row.appointment_id for row in rows]
    linked = (
        set(
            db.scalars(
                select(R.acuity_appointment_id).where(
                    R.acuity_appointment_id.in_(ids), R.id != card.id
                )
            )
        )
        if ids
        else set()
    )

    return [
        BookingMatch(
            appointment_id=row.appointment_id,
            first_name=row.first_name,
            last_name=row.last_name,
            appointment_type=row.appointment_type,
            date=row.appointment_datetime or row.date,
            time=row.time,
            canceled=bool(row.canceled),
            already_linked=row.appointment_id in linked,
        )
        for row in rows
    ]
