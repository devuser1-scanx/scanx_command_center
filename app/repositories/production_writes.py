"""
The two explicitly-approved exceptions to Command Center's normal read-only
access to the production database - see app/db/prod_session.py. Both record
things Command Center itself just did (sent an SMS, sent a form link) into
tables production's other systems already write to and other parts of this
app already read from (list_messages, list_form_tracking), so Command
Center's own sends show up in the same history.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.production import FormTracking, Message


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
        timestamp=datetime.now(UTC),
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
    """Records that a form link was sent. ON CONFLICT (appointment_id,
    form_type) DO NOTHING relies on form_tracking's own existing unique
    constraint (uq_form_tracking), so re-sending the same form doesn't
    create a duplicate row; returns None when a row already existed.
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

    row = prod_db.execute(statement).scalar_one_or_none()

    return row
