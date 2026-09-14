from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.integrations.gcs_reports import locate_report_blob_for_appointment
from app.integrations.pdf_proxy_client import PdfProxyApiError, create_short_url
from app.schemas.report_links import ReportLinkResponse

# Matches the scanx-pdf-proxy service's own LINK_EXPIRY_MINUTES constant.
# That service is the source of truth for when a link actually expires;
# this is only used to tell the UI when to expect that to happen.
REPORT_LINK_EXPIRE_DAYS = 7


def create_report_link_for_appointment(
    prod_db: Session,
    *,
    appointment_id: str,
) -> ReportLinkResponse:
    """Finds the appointment's report in GCS (the same lookup the fax/mail
    send flows already use) and asks the scanx-pdf-proxy service for a
    short, self-expiring download link to it.
    """
    blob = locate_report_blob_for_appointment(prod_db, appointment_id)

    if blob is None or not blob.name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report was found for this appointment.",
        )

    try:
        short_url = create_short_url(gcs_blob_name=blob.name)
    except PdfProxyApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not generate a report link: {exc.message}",
        ) from exc

    return ReportLinkResponse(
        url=short_url,
        expires_at=datetime.now(UTC) + timedelta(days=REPORT_LINK_EXPIRE_DAYS),
    )
