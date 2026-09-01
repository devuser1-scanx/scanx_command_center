from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.twilio_client import TwilioApiError, send_sms
from app.models.auth import CCUser
from app.repositories.patients import get_appointment_by_appointment_id, get_clinic
from app.repositories.sms import create_sms_transmission
from app.schemas.sms import SendSmsResponse, SmsPrefillResponse


def get_sms_prefill(prod_db: Session, appointment_id: str) -> SmsPrefillResponse:
    """Looks up the patient's phone number and, if their appointment has a
    clinic on file, that clinic's Google Maps link - used to prefill the
    "To" field and the Directions message template.
    """
    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    if appointment is None:
        return SmsPrefillResponse(phone=None, directions_link=None)

    clinic = get_clinic(prod_db, appointment.clinic_id) if appointment.clinic_id else None

    return SmsPrefillResponse(
        phone=appointment.phone,
        directions_link=clinic.map_link if clinic else None,
    )


def send_patient_sms(
    db: Session,
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
        message_sid = send_sms(to=destination_number, body=body)
        transmission_status = "submitted"
        error_message = None
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except TwilioApiError as exc:
        message_sid = None
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

    return SendSmsResponse(
        id=row.id,
        destination_number=row.destination_number,
        status=row.status,
        twilio_message_sid=row.twilio_message_sid,
        error_message=row.error_message,
        created_at=row.created_at,
    )
