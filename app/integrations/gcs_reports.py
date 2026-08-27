from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from google.cloud import storage
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.westfax_client import FaxAttachment
from app.models.production import Appointment
from app.repositories.patients import get_appointment_by_appointment_id

# Matches the human-readable tricefy report filename (e.g.
# "Soro_Abrama_Report_2025-12-30.pdf"), as opposed to the DICOM-UID-named
# file that sits alongside it in the same folder (e.g.
# "1.3.6.1.4.1.35190.1.1.20251230.59970055080.pdf").
_TRICEFY_HUMAN_READABLE_FILE = re.compile(r".+_\d{4}-\d{2}-\d{2}\.pdf$", re.IGNORECASE)

_FIBROSCAN_PREFIX = "fibroscan/"
_TRICEFY_PREFIX = "tricefy/"


def _blob_filename(blob: storage.Blob) -> str:
    """Blobs returned from listing always have a name; this just satisfies
    the SDK's `str | None` typing.
    """
    return (blob.name or "").rsplit("/", 1)[-1]


@lru_cache
def _get_bucket() -> storage.Bucket:
    client = storage.Client()
    return client.bucket(settings.gcs_reports_bucket)


def locate_fibroscan_blob(appointment_id: str) -> storage.Blob | None:
    """Exact match only: `fibroscan/*_{appointment_id}.pdf`.

    Some FibroScan reports use a different device-code+timestamp naming
    scheme with no appointment_id in the filename at all - those simply
    won't match here, which is correct (never guess).
    """
    bucket = _get_bucket()

    matches = list(
        bucket.list_blobs(
            prefix=_FIBROSCAN_PREFIX,
            match_glob=f"{_FIBROSCAN_PREFIX}*_{appointment_id}.pdf",
        )
    )

    if len(matches) != 1:
        return None

    return matches[0]


def locate_tricefy_blob(
    *,
    first_name: str,
    last_name: str,
    exam_date: date,
) -> storage.Blob | None:
    """Matches a tricefy folder by exact LAST^FIRST^..._{YYYYMMDD}/ name,
    then picks the human-readable file inside it. Returns None on any
    ambiguity (zero or multiple folder/file matches) rather than guessing.
    """
    bucket = _get_bucket()

    folder_marker = f"{last_name.strip().upper()}^{first_name.strip().upper()}^"
    date_suffix = f"_{exam_date.strftime('%Y%m%d')}/"

    folders_iterator = bucket.list_blobs(prefix=_TRICEFY_PREFIX, delimiter="/")
    list(folders_iterator)  # Must be exhausted before .prefixes is populated.

    matching_folders = [
        prefix
        for prefix in folders_iterator.prefixes
        if prefix[len(_TRICEFY_PREFIX) :].startswith(folder_marker) and prefix.endswith(date_suffix)
    ]

    if len(matching_folders) != 1:
        return None

    folder = matching_folders[0]

    candidate_files = [
        blob
        for blob in bucket.list_blobs(prefix=folder)
        if _TRICEFY_HUMAN_READABLE_FILE.match(_blob_filename(blob))
    ]

    if len(candidate_files) != 1:
        return None

    return candidate_files[0]


def locate_report_blob(appointment: Appointment) -> storage.Blob | None:
    appointment_type = (appointment.appointment_type or "").lower()

    if "fibro" in appointment_type:
        return locate_fibroscan_blob(appointment.appointment_id)

    exam_date = (
        appointment.appointment_datetime.date()
        if appointment.appointment_datetime
        else appointment.date.date()
        if appointment.date
        else None
    )

    if not exam_date or not appointment.first_name or not appointment.last_name:
        return None

    return locate_tricefy_blob(
        first_name=appointment.first_name,
        last_name=appointment.last_name,
        exam_date=exam_date,
    )


def download_blob_as_attachment(blob: storage.Blob) -> FaxAttachment:
    return FaxAttachment(
        filename=_blob_filename(blob),
        content=blob.download_as_bytes(),
        content_type="application/pdf",
    )


def locate_report_blob_for_appointment(
    prod_db: Session,
    appointment_id: str,
) -> storage.Blob | None:
    """Shared by the fax and mail send flows: look up the appointment, then
    locate its report blob (if any) via locate_report_blob.
    """
    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    if appointment is None:
        return None

    return locate_report_blob(appointment)


def fetch_report_attachment_for_appointment(
    prod_db: Session,
    appointment_id: str,
) -> FaxAttachment | None:
    """Shared by the fax and mail send flows: look up the appointment,
    locate its report blob, and download it as an attachment.
    """
    blob = locate_report_blob_for_appointment(prod_db, appointment_id)

    if blob is None:
        return None

    return download_blob_as_attachment(blob)
