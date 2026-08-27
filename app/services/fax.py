from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.gcs_reports import (
    fetch_report_attachment_for_appointment,
    locate_report_blob_for_appointment,
)
from app.integrations.westfax_client import (
    FaxAttachment,
    WestFaxApiError,
    get_westfax_client,
)
from app.models.auth import CCUser
from app.repositories.fax import create_fax_transmission
from app.schemas.fax import FaxReportLookupResponse, FaxTransmissionItem, SendFaxResponse

MAX_FAX_ATTACHMENTS = 2


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

    try:
        client = get_westfax_client()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    try:
        westfax_job_id = client.send_fax(
            numbers=[destination_number],
            files=attachments,
            header=settings.westfax_fax_header,
            billing_code=appointment_id,
            job_name=f"{appointment_id}-{datetime.utcnow().isoformat()}",
        )
        transmission_status = "submitted"
        error_message = None
    except WestFaxApiError as exc:
        westfax_job_id = None
        transmission_status = "failed_to_submit"
        error_message = exc.message

    rows = [
        create_fax_transmission(
            db,
            appointment_id=appointment_id,
            destination_number=destination_number,
            file_name=attachment.filename,
            status=transmission_status,
            westfax_job_id=westfax_job_id,
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
            detail=f"WestFax could not accept the fax: {error_message}",
        )

    return SendFaxResponse(
        destination_number=destination_number,
        westfax_job_id=westfax_job_id,
        transmissions=[
            FaxTransmissionItem(
                id=row.id,
                file_name=row.file_name,
                status=row.status,
                westfax_job_id=row.westfax_job_id,
                error_message=row.error_message,
            )
            for row in rows
        ],
        created_at=rows[0].created_at,
    )
