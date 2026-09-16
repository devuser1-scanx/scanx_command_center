from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.integrations.jotform_links import build_transvag_consent_form_url
from app.repositories.patients import get_appointment_by_appointment_id, get_patient_intake
from app.schemas.transvag_form_links import TransvagFormLinkResponse


def create_transvag_form_link_for_appointment(
    prod_db: Session,
    *,
    appointment_id: str,
) -> TransvagFormLinkResponse:
    """Builds a prefilled JotForm Transvaginal Consent form link for this
    appointment, using the patient's name, dob, and appointment_id.
    """
    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found.",
        )

    intake = get_patient_intake(prod_db, appointment_id)

    url = build_transvag_consent_form_url(
        first_name=appointment.first_name,
        last_name=appointment.last_name,
        dob=intake.dob if intake else None,
        appointment_id=appointment.appointment_id,
    )

    return TransvagFormLinkResponse(url=url)
