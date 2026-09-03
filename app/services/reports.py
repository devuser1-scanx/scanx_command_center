from __future__ import annotations

import base64
import binascii
from datetime import datetime

from fastapi import HTTPException, status

from app.integrations.gcs_reports import (
    FIBROSCAN_PREFIX,
    TRICEFY_PREFIX,
    get_report_file_blob,
    list_group_files,
    list_report_groups,
)
from app.schemas.reports import (
    ReportFileItem,
    ReportGroupFilesResponse,
    ReportGroupItem,
    ReportGroupListResponse,
)

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")


def _encode_id(*parts: str) -> str:
    raw = "\x1f".join(parts)  # unit-separator: safe, since none of our parts contain it
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_id(encoded: str, expected_parts: int) -> list[str]:
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise _NOT_FOUND from exc

    parts = raw.split("\x1f")

    if len(parts) != expected_parts or not all(parts):
        raise _NOT_FOUND

    return parts


def _display_name(source: str, group_key: str) -> str:
    if source == "fibroscan":
        return group_key.removesuffix(".pdf")

    return group_key


def list_reports(*, search: str | None, page: int, page_size: int) -> ReportGroupListResponse:
    groups = list_report_groups()

    if search:
        needle = search.strip().lower()
        groups = [group for group in groups if needle in _display_name(group.source, group.group_key).lower()]

    groups = sorted(groups, key=lambda group: group.updated_at or datetime.min, reverse=True)

    total = len(groups)
    start = (page - 1) * page_size
    page_groups = groups[start : start + page_size]

    items = [
        ReportGroupItem(
            id=_encode_id(group.source, group.group_key),
            source=group.source,  # type: ignore[arg-type]
            name=_display_name(group.source, group.group_key),
            file_count=group.file_count,
            updated_at=group.updated_at,
        )
        for group in page_groups
    ]

    return ReportGroupListResponse(items=items, total=total, page=page, page_size=page_size)


def get_report_group_files(group_id: str) -> ReportGroupFilesResponse:
    source, group_key = _decode_id(group_id, expected_parts=2)

    if source not in ("tricefy", "fibroscan"):
        raise _NOT_FOUND

    files = list_group_files(source, group_key)

    if not files:
        raise _NOT_FOUND

    return ReportGroupFilesResponse(
        group_id=group_id,
        group_name=_display_name(source, group_key),
        source=source,  # type: ignore[arg-type]
        files=[
            ReportFileItem(
                id=_encode_id(file.blob_name),
                file_name=file.file_name,
                size_bytes=file.size_bytes,
                updated_at=file.updated_at,
            )
            for file in files
        ],
    )


def get_report_file_content(group_id: str, file_id: str) -> tuple[bytes, str]:
    source, group_key = _decode_id(group_id, expected_parts=2)
    (blob_name,) = _decode_id(file_id, expected_parts=1)

    expected_prefix = f"{TRICEFY_PREFIX}{group_key}/" if source == "tricefy" else FIBROSCAN_PREFIX

    if not blob_name.startswith(expected_prefix):
        raise _NOT_FOUND

    blob = get_report_file_blob(blob_name)

    if blob is None:
        raise _NOT_FOUND

    return blob.download_as_bytes(), blob_name.rsplit("/", 1)[-1]
