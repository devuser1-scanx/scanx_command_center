from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.jotform_links import format_appointment_date, format_dob
from app.integrations.twilio_client import TwilioApiError, send_sms
from app.models.auth import CCUser
from app.repositories.google_reviews import get_google_review_url
from app.repositories.patients import (
    get_appointment_by_appointment_id,
    get_clinic,
    get_patient_intake,
)
from app.repositories.production_writes import insert_form_tracking, insert_outbound_message
from app.repositories.sms import create_sms_transmission
from app.schemas.sms import SendSmsResponse, SmsPrefillResponse
from app.services.dashboard import build_patient_name

logger = logging.getLogger(__name__)

# SMS purposes that also send a JotForm link and so also get a form_tracking
# row recorded in production (see _record_sent_form_link below). Extend this
# as more form-link buttons (scrotal_consent, transvaginal, ...) are wired
# up - the SMS purpose value on the left, form_tracking's own form_type
# value on the right.
FORM_TRACKING_FORM_TYPES: dict[str, str] = {
    "pcp_form": "pcp_declaration",
    "scrotal_form": "scrotal_consent",
    "transvag_form": "transvag_consent",
}


def get_sms_prefill(
    db: Session,
    prod_db: Session,
    appointment_id: str,
) -> SmsPrefillResponse:
    """Looks up the patient's phone number and, if their appointment has a
    clinic on file, that clinic's Google Maps link and Google review link -
    used to prefill the "To" field and the Directions/Ask For Review message
    templates.

    The Google review link lives in Command Center's own database
    (cc_google_reviews), not the production one the appointment/clinic
    lookup uses - see app/models/google_reviews.py.
    """
    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    if appointment is None:
        return SmsPrefillResponse(
            phone=None,
            directions_link=None,
            google_review_link=None,
        )

    clinic = get_clinic(prod_db, appointment.clinic_id) if appointment.clinic_id else None
    google_review_link = (
        get_google_review_url(db, appointment.clinic_id) if appointment.clinic_id else None
    )

    return SmsPrefillResponse(
        phone=appointment.phone,
        directions_link=clinic.map_link if clinic else None,
        google_review_link=google_review_link,
    )


def _record_sent_message(
    prod_db: Session,
    *,
    appointment_id: str,
    body: str,
    message_sid: str | None,
    from_number: str | None,
    destination_number: str,
) -> None:
    """Best-effort: mirrors this send into production's own `messages`
    table so it shows up in the same history other systems already write
    to. Never raises - the SMS already went out via Twilio and that's the
    part that matters to the caller; a bookkeeping failure here shouldn't
    turn into a user-facing error.
    """
    try:
        insert_outbound_message(
            prod_db,
            appointment_id=appointment_id,
            body=body,
            status="sent",
            message_sid=message_sid,
            sender=from_number,
            recipient=destination_number,
        )
        prod_db.commit()
    except Exception:
        prod_db.rollback()
        logger.exception(
            "Failed to record sent SMS into production messages table (appointment_id=%s)",
            appointment_id,
        )


def _record_sent_form_link(
    prod_db: Session,
    *,
    appointment_id: str,
    purpose: str,
) -> None:
    """Best-effort, same rationale as _record_sent_message. Only runs for
    purposes that actually send a form link (FORM_TRACKING_FORM_TYPES).
    """
    form_type = FORM_TRACKING_FORM_TYPES.get(purpose)

    if form_type is None:
        return

    try:
        appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

        if appointment is None:
            return

        intake = get_patient_intake(prod_db, appointment_id)

        insert_form_tracking(
            prod_db,
            appointment_id=appointment_id,
            patient_name=build_patient_name(appointment),
            dob=format_dob(intake.dob if intake else None),
            appointment_date=format_appointment_date(appointment.appointment_datetime),
            appointment_type=appointment.appointment_type or appointment.category or "Exam",
            form_type=form_type,
        )
        prod_db.commit()
    except Exception:
        prod_db.rollback()
        logger.exception(
            "Failed to record sent form link into production form_tracking table "
            "(appointment_id=%s, purpose=%s)",
            appointment_id,
            purpose,
        )


def send_patient_sms(
    db: Session,
    prod_db: Session,
    *,
    appointment_id: str,
    purpose: str,
    destination_number: str,
    body: str,
    actor: CCUser,
) -> SendSmsResponse:
    destination_number = destination_number.strip()
    body = body.strip()

    if not destination_number:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A destination phone number is required.",
        )

    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A message body is required.",
        )

    try:
        twilio_result = send_sms(to=destination_number, body=body)
        message_sid = twilio_result.message_sid
        from_number = twilio_result.from_number
        transmission_status = "submitted"
        error_message = None
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except TwilioApiError as exc:
        message_sid = None
        from_number = None
        transmission_status = "failed_to_submit"
        error_message = exc.message

    row = create_sms_transmission(
        db,
        appointment_id=appointment_id,
        purpose=purpose,
        destination_number=destination_number,
        body=body,
        status=transmission_status,
        twilio_message_sid=message_sid,
        error_message=error_message,
        sent_by_user_id=actor.id,
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The text message could not be recorded because of a conflicting record.",
        ) from exc

    if transmission_status == "failed_to_submit":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Twilio could not accept the message: {error_message}",
        )

    # Only mirror into production once Twilio actually accepted the
    # message - a failed send has nothing worth recording as "sent".
    _record_sent_message(
        prod_db,
        appointment_id=appointment_id,
        body=body,
        message_sid=message_sid,
        from_number=from_number,
        destination_number=destination_number,
    )
    _record_sent_form_link(prod_db, appointment_id=appointment_id, purpose=purpose)

    return SendSmsResponse(
        id=row.id,
        destination_number=row.destination_number,
        status=row.status,
        twilio_message_sid=row.twilio_message_sid,
        error_message=row.error_message,
        created_at=row.created_at,
    )
