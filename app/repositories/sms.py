from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.sms import CCSmsTransmission


def create_sms_transmission(
    db: Session,
    *,
    appointment_id: str,
    purpose: str,
    destination_number: str,
    body: str,
    status: str,
    twilio_message_sid: str | None,
    error_message: str | None,
    sent_by_user_id: int,
) -> CCSmsTransmission:
    transmission = CCSmsTransmission(
        appointment_id=appointment_id,
        purpose=purpose,
        destination_number=destination_number,
        body=body,
        status=status,
        twilio_message_sid=twilio_message_sid,
        error_message=error_message,
        sent_by_user_id=sent_by_user_id,
    )

    db.add(transmission)
    db.flush()

    return transmission
