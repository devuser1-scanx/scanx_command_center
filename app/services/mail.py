from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.gcs_reports import fetch_report_attachment_for_appointment
from app.integrations.gmail_client import GmailApiError, send_email
from app.integrations.westfax_client import FaxAttachment
from app.models.auth import CCUser
from app.repositories.mail import create_mail_transmission
from app.schemas.mail import MailTransmissionItem, SendMailResponse

MAX_MAIL_ATTACHMENTS = 2


def _parse_addresses(raw: str | None) -> list[str]:
    if not raw:
        return []

    return [address.strip() for address in raw.split(",") if address.strip()]


def send_patient_mail(
    db: Session,
    prod_db: Session,
    *,
    appointment_id: str,
    to_addresses: str,
    cc_addresses: str | None,
    bcc_addresses: str | None,
    subject: str,
    body_html: str,
    include_report: bool,
    uploaded_files: list[UploadFile],
    actor: CCUser,
) -> SendMailResponse:
    to_list = _parse_addresses(to_addresses)
    cc_list = _parse_addresses(cc_addresses)
    bcc_list = _parse_addresses(bcc_addresses)

    if not to_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one recipient email address is required.",
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

    if len(attachments) > MAX_MAIL_ATTACHMENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"An email can include at most {MAX_MAIL_ATTACHMENTS} files.",
        )

    try:
        gmail_message_id = send_email(
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            subject=subject,
            html_body=body_html,
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
        gmail_message_id = None
        transmission_status = "failed_to_submit"
        error_message = exc.message

    to_joined = ", ".join(to_list)
    cc_joined = ", ".join(cc_list) if cc_list else None
    bcc_joined = ", ".join(bcc_list) if bcc_list else None

    file_names = [attachment.filename for attachment in attachments] or ["(no attachment)"]

    rows = [
        create_mail_transmission(
            db,
            appointment_id=appointment_id,
            to_addresses=to_joined,
            cc_addresses=cc_joined,
            bcc_addresses=bcc_joined,
            subject=subject,
            file_name=file_name,
            status=transmission_status,
            gmail_message_id=gmail_message_id,
            error_message=error_message,
            sent_by_user_id=actor.id,
        )
        for file_name in file_names
    ]

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The email could not be recorded because of a conflicting record.",
        ) from exc

    if transmission_status == "failed_to_submit":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gmail could not accept the email: {error_message}",
        )

    return SendMailResponse(
        to_addresses=to_joined,
        gmail_message_id=gmail_message_id,
        transmissions=[
            MailTransmissionItem(
                id=row.id,
                file_name=row.file_name,
                status=row.status,
                gmail_message_id=row.gmail_message_id,
                error_message=row.error_message,
            )
            for row in rows
        ],
        created_at=rows[0].created_at,
    )
