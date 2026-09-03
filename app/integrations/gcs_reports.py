from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
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

# Public aliases for callers outside this module (e.g. the reports browsing
# service), which need the prefixes without reaching into "private" names.
FIBROSCAN_PREFIX = _FIBROSCAN_PREFIX
TRICEFY_PREFIX = _TRICEFY_PREFIX


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


# --- Report browsing (Reports tab) -----------------------------------------
#
# Unlike the exact-match lookups above, this section lists everything in
# both report prefixes for the browsable Reports tab. Tricefy stores one
# subfolder per exam (holding a DICOM-UID-named PDF plus a human-readable
# one); fibroscan stores PDFs flat with no subfolders. To give the UI one
# consistent "folder" concept, each fibroscan PDF is treated as its own
# single-file group.


@dataclass(frozen=True)
class ReportGroup:
    source: str  # "tricefy" | "fibroscan"
    group_key: str  # unique within its source: tricefy folder name, or fibroscan file name
    file_count: int
    updated_at: datetime | None


@dataclass(frozen=True)
class ReportFile:
    blob_name: str
    file_name: str
    size_bytes: int
    updated_at: datetime | None


_REPORT_GROUPS_CACHE_TTL_SECONDS = 300
_report_groups_cache: tuple[float, list[ReportGroup]] | None = None


def _list_tricefy_groups(bucket: storage.Bucket) -> list[ReportGroup]:
    groups: dict[str, list[storage.Blob]] = {}

    for blob in bucket.list_blobs(prefix=_TRICEFY_PREFIX):
        relative = (blob.name or "")[len(_TRICEFY_PREFIX) :]
        folder_name, _, file_name = relative.partition("/")

        if not folder_name or not file_name:
            continue  # stray object directly under tricefy/, not a report

        groups.setdefault(folder_name, []).append(blob)

    return [
        ReportGroup(
            source="tricefy",
            group_key=folder_name,
            file_count=len(blobs),
            updated_at=max((blob.updated for blob in blobs if blob.updated), default=None),
        )
        for folder_name, blobs in groups.items()
    ]


def _list_fibroscan_groups(bucket: storage.Bucket) -> list[ReportGroup]:
    groups = []

    for blob in bucket.list_blobs(prefix=_FIBROSCAN_PREFIX):
        file_name = _blob_filename(blob)

        if not file_name.lower().endswith(".pdf"):
            continue  # skip non-report device files (e.g. .fibx2)

        groups.append(
            ReportGroup(
                source="fibroscan",
                group_key=file_name,
                file_count=1,
                updated_at=blob.updated,
            )
        )

    return groups


def list_report_groups() -> list[ReportGroup]:
    """All report groups (tricefy exam folders + individual fibroscan PDFs)
    across both prefixes, combined. Cached briefly since building this list
    walks both prefixes in full on every miss.
    """
    global _report_groups_cache

    if _report_groups_cache is not None:
        cached_at, cached_groups = _report_groups_cache

        if time.monotonic() - cached_at < _REPORT_GROUPS_CACHE_TTL_SECONDS:
            return cached_groups

    bucket = _get_bucket()
    groups = _list_tricefy_groups(bucket) + _list_fibroscan_groups(bucket)
    _report_groups_cache = (time.monotonic(), groups)

    return groups


def list_group_files(source: str, group_key: str) -> list[ReportFile]:
    """All PDF files inside one report group."""
    bucket = _get_bucket()

    if source == "tricefy":
        blobs = [
            blob
            for blob in bucket.list_blobs(prefix=f"{_TRICEFY_PREFIX}{group_key}/")
            if _blob_filename(blob).lower().endswith(".pdf")
        ]
    elif source == "fibroscan":
        blob = bucket.get_blob(f"{_FIBROSCAN_PREFIX}{group_key}")
        blobs = [blob] if blob is not None else []
    else:
        blobs = []

    return [
        ReportFile(
            blob_name=blob.name or "",
            file_name=_blob_filename(blob),
            size_bytes=blob.size or 0,
            updated_at=blob.updated,
        )
        for blob in blobs
    ]


def get_report_file_blob(blob_name: str) -> storage.Blob | None:
    """Fetches a single report blob by its full bucket path, restricted to
    the tricefy/fibroscan report prefixes so this can't be used to read
    unrelated bucket contents (e.g. assets/, clinicpricing/, raw/).
    """
    if not (blob_name.startswith(_TRICEFY_PREFIX) or blob_name.startswith(_FIBROSCAN_PREFIX)):
        return None

    return _get_bucket().get_blob(blob_name)
