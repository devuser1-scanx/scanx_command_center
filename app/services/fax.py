from __future__ import annotations

import re

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.gcs_reports import (
    fetch_report_attachment_for_appointment,
    locate_report_blob_for_appointment,
)
from app.integrations.gmail_client import GmailApiError, send_email
from app.integrations.westfax_client import FaxAttachment
from app.models.auth import CCUser
from app.models.production import Appointment
from app.repositories.fax import create_fax_transmission
from app.repositories.patients import get_appointment_by_appointment_id
from app.schemas.fax import FaxReportLookupResponse, FaxTransmissionItem, SendFaxResponse

MAX_FAX_ATTACHMENTS = 2

# WestFax's email-to-fax gateway: an email to {digits}@westfax.com is faxed
# to that number. Confirmed via DNS that westfax.com (not westfa.com) has
# WestFax's real mail servers.
WESTFAX_EMAIL_TO_FAX_DOMAIN = "westfax.com"


def _normalize_fax_number(raw: str) -> str:
    return re.sub(r"\D", "", raw)


def _build_fax_subject(appointment: Appointment | None) -> str:
    """firstname_lastname_Fibroscan-Report for FibroScan / Liver
    Elastography appointments, firstname_lastname_<appointment type>-Report
    for everything else. Falls back to fax_email_subject if the
    appointment - or its name/type - isn't available.
    """
    if appointment is None:
        return settings.fax_email_subject

    first_name = (appointment.first_name or "").strip()
    last_name = (appointment.last_name or "").strip()
    name_part = "_".join(part for part in (first_name, last_name) if part)

    appointment_type = (appointment.appointment_type or "").strip()

    if not name_part or not appointment_type:
        return settings.fax_email_subject

    report_type = "Fibroscan" if "fibro" in appointment_type.lower() else appointment_type

    return f"{name_part}_{report_type}-Report"


def lookup_patient_report(
    prod_db: Session,
    appointment_id: str,
) -> FaxReportLookupResponse:
    """Metadata-only lookup (no download) so the popup can show what will
    be attached before the user clicks Send. Shared by the mail feature too.
    """
    blob = locate_report_blob_for_appointment(prod_db, appointment_id)

    if blob is None:
        return FaxReportLookupResponse(found=False, file_name=None)

    return FaxReportLookupResponse(
        found=True,
        file_name=blob.name.rsplit("/", 1)[-1] if blob.name else None,
    )


def send_patient_fax(
    db: Session,
    prod_db: Session,
    *,
    appointment_id: str,
    destination_number: str,
    subject: str | None,
    include_report: bool,
    uploaded_files: list[UploadFile],
    actor: CCUser,
) -> SendFaxResponse:
    destination_number = destination_number.strip()

    if not destination_number:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A destination fax number is required.",
        )

    normalized_number = _normalize_fax_number(destination_number)

    if not normalized_number:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The destination fax number must contain digits.",
        )

    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    attachments: list[FaxAttachment] = []

    if include_report:
        report_attachment = fetch_report_attachment_for_appointment(prod_db, appointment_id)

        if report_attachment is not None:
            attachments.append(report_attachment)

    for uploaded_file in uploaded_files:
        content = uploaded_file.file.read()

        if not content:
            continue

        attachments.append(
            FaxAttachment(
                filename=uploaded_file.filename or "document",
                content=content,
                content_type=uploaded_file.content_type or "application/octet-stream",
            )
        )

    if not attachments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file (a report or an upload) is required to send a fax.",
        )

    if len(attachments) > MAX_FAX_ATTACHMENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A fax can include at most {MAX_FAX_ATTACHMENTS} files.",
        )

    fax_email_address = f"{normalized_number}@{WESTFAX_EMAIL_TO_FAX_DOMAIN}"
    resolved_subject = (
        subject.strip() if subject and subject.strip() else _build_fax_subject(appointment)
    )

    try:
        email_message_id = send_email(
            to=[fax_email_address],
            cc=[settings.fax_feedback_email],
            bcc=[],
            subject=resolved_subject,
            html_body="",
            attachments=attachments,
        )
        transmission_status = "submitted"
        error_message = None
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except GmailApiError as exc:
        email_message_id = None
        transmission_status = "failed_to_submit"
        error_message = exc.message

    rows = [
        create_fax_transmission(
            db,
            appointment_id=appointment_id,
            destination_number=destination_number,
            file_name=attachment.filename,
            status=transmission_status,
            email_message_id=email_message_id,
            error_message=error_message,
            sent_by_user_id=actor.id,
        )
        for attachment in attachments
    ]

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The fax could not be recorded because of a conflicting record.",
        ) from exc

    if transmission_status == "failed_to_submit":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The fax email could not be sent: {error_message}",
        )

    return SendFaxResponse(
        destination_number=destination_number,
        email_message_id=email_message_id,
        transmissions=[
            FaxTransmissionItem(
                id=row.id,
                file_name=row.file_name,
                status=row.status,
                email_message_id=row.email_message_id,
                error_message=row.error_message,
            )
            for row in rows
        ],
        created_at=rows[0].created_at,
    )
