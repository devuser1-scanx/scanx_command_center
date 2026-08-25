from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.fax import CCFaxTransmission


def create_fax_transmission(
    db: Session,
    *,
    appointment_id: str,
    destination_number: str,
    file_name: str,
    status: str,
    westfax_job_id: str | None,
    error_message: str | None,
    sent_by_user_id: int,
) -> CCFaxTransmission:
    transmission = CCFaxTransmission(
        appointment_id=appointment_id,
        destination_number=destination_number,
        file_name=file_name,
        status=status,
        westfax_job_id=westfax_job_id,
        error_message=error_message,
        sent_by_user_id=sent_by_user_id,
    )

    db.add(transmission)
    db.flush()

    return transmission
