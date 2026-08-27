from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.mail import CCMailTransmission


def create_mail_transmission(
    db: Session,
    *,
    appointment_id: str,
    to_addresses: str,
    cc_addresses: str | None,
    bcc_addresses: str | None,
    subject: str,
    file_name: str,
    status: str,
    gmail_message_id: str | None,
    error_message: str | None,
    sent_by_user_id: int,
) -> CCMailTransmission:
    transmission = CCMailTransmission(
        appointment_id=appointment_id,
        to_addresses=to_addresses,
        cc_addresses=cc_addresses,
        bcc_addresses=bcc_addresses,
        subject=subject,
        file_name=file_name,
        status=status,
        gmail_message_id=gmail_message_id,
        error_message=error_message,
        sent_by_user_id=sent_by_user_id,
    )

    db.add(transmission)
    db.flush()

    return transmission
