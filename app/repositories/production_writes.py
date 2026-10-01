"""
The explicitly-approved exceptions to Command Center's normal read-only access
to the production database — see app/db/prod_session.py.

These writes are intentionally isolated in this module so production mutations
remain easy to audit and review.

Approved writes:

1. Record an SMS sent by Command Center in the existing `messages` table.
2. Record a form link sent by Command Center in the existing `form_tracking`
   table.
3. Record a manual patient check-in performed through Command Center by:
   - updating the existing `appointment` row; and
   - inserting/updating the existing `checkins` row.

Do not add additional production writes outside this module without an explicit
architectural decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.production import (
    Appointment,
    Checkin,
    FormTracking,
    Message,
)


@dataclass(frozen=True)
class ManualCheckInWriteResult:
    appointment: Appointment
    checkin: Checkin | None
    checked_in_at: datetime
    already_checked_in: bool


def _utc_now_naive() -> datetime:
    """
    Returns the current UTC timestamp without tzinfo.

    The production columns used by the manual check-in workflow
    (`appointment.checked_in_at`, `appointment.updated_at`, and
    `checkins.checkin_time`) are PostgreSQL `timestamp without time zone`
    columns.

    We therefore explicitly store a naive datetime whose value represents UTC
    rather than allowing the application/server's local timezone to influence
    the stored value.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def insert_outbound_message(
    prod_db: Session,
    *,
    appointment_id: str,
    body: str,
    status: str,
    message_sid: str | None,
    sender: str | None,
    recipient: str | None,
) -> Message:
    message = Message(
        appointment_id=appointment_id,
        direction="outbound",
        channel="twilio",
        message_sid=message_sid,
        body=body,
        status=status,
        sender=sender,
        recipient=recipient,
        timestamp=_utc_now_naive(),
    )

    prod_db.add(message)
    prod_db.flush()

    return message


def insert_form_tracking(
    prod_db: Session,
    *,
    appointment_id: str,
    patient_name: str,
    dob: str | None,
    appointment_date: str | None,
    appointment_type: str | None,
    form_type: str,
) -> FormTracking | None:
    """
    Records that a form link was sent.

    ON CONFLICT (appointment_id, form_type) DO NOTHING relies on
    form_tracking's existing `uq_form_tracking` unique constraint, so
    re-sending the same form does not create a duplicate row.

    Returns None when the row already existed.
    """
    statement = (
        pg_insert(FormTracking)
        .values(
            appointment_id=appointment_id,
            patient_name=patient_name,
            dob=dob,
            appointment_date=appointment_date,
            appointment_type=appointment_type,
            form_type=form_type,
            status="Pending",
            sent_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_form_tracking")
        .returning(FormTracking)
    )

    return prod_db.execute(statement).scalar_one_or_none()


def manual_check_in_appointment(
    prod_db: Session,
    *,
    appointment_id: str,
    location: str | None,
    checked_in_at: datetime | None = None,
) -> ManualCheckInWriteResult | None:
    """
    Persist a manual Command Center patient check-in.

    This function performs only production-database persistence. It does NOT:

    - commit the transaction;
    - send Google Chat notifications;
    - update Acuity;
    - send Twilio SMS;
    - write Command Center audit rows.

    Those operations belong to the service/integration layers.

    The appointment row is locked using SELECT ... FOR UPDATE so two concurrent
    manual-check-in requests cannot both transition the appointment from
    unchecked to checked-in and subsequently trigger duplicate external side
    effects.

    Returns:
        None:
            No appointment exists for the supplied appointment_id.

        ManualCheckInWriteResult(already_checked_in=True):
            The appointment was already checked in. No production values are
            changed.

        ManualCheckInWriteResult(already_checked_in=False):
            The appointment and checkins rows were updated using one identical
            UTC timestamp.
    """

    appointment = prod_db.execute(
        select(Appointment).where(Appointment.appointment_id == appointment_id).with_for_update()
    ).scalar_one_or_none()

    if appointment is None:
        return None

    if appointment.checkin is True:
        existing_checkin = prod_db.execute(
            select(Checkin).where(Checkin.appointment_id == appointment_id)
        ).scalar_one_or_none()

        existing_checked_in_at = (
            appointment.checked_in_at
            or (existing_checkin.checkin_time if existing_checkin is not None else None)
            or _utc_now_naive()
        )

        return ManualCheckInWriteResult(
            appointment=appointment,
            checkin=existing_checkin,
            checked_in_at=existing_checked_in_at,
            already_checked_in=True,
        )

    timestamp = checked_in_at or _utc_now_naive()

    # These production columns are `timestamp without time zone`. If a
    # timezone-aware timestamp is supplied by a caller, normalize it to UTC and
    # remove tzinfo before persistence.
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)

    appointment.checkin = True
    appointment.checked_in_at = timestamp
    appointment.updated_at = timestamp

    prod_db.flush()

    checkin_statement = (
        pg_insert(Checkin)
        .values(
            appointment_id=appointment_id,
            checkin_time=timestamp,
            location=location,
            status="Success",
        )
        .on_conflict_do_update(
            index_elements=[Checkin.appointment_id],
            set_={
                "checkin_time": timestamp,
                "location": location,
                "status": "Success",
            },
        )
        .returning(Checkin)
    )

    checkin = prod_db.execute(checkin_statement).scalar_one()

    prod_db.flush()

    return ManualCheckInWriteResult(
        appointment=appointment,
        checkin=checkin,
        checked_in_at=timestamp,
        already_checked_in=False,
    )
