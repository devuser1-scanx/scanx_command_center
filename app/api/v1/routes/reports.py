from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies.auth import require_permission
from app.models.auth import CCUser
from app.schemas.reports import ReportGroupFilesResponse, ReportGroupListResponse
from app.services.reports import (
    get_report_file_content,
    get_report_group_files,
    list_reports,
)

router = APIRouter(prefix="/reports")

MAX_PAGE_SIZE = 100


@router.get("/groups", response_model=ReportGroupListResponse)
def list_report_groups_route(
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    current_user: CCUser = Depends(require_permission("reports.view")),
) -> ReportGroupListResponse:
    return list_reports(search=search, page=page, page_size=page_size)


@router.get("/groups/{group_id}/files", response_model=ReportGroupFilesResponse)
def get_report_group_files_route(
    group_id: str,
    current_user: CCUser = Depends(require_permission("reports.view")),
) -> ReportGroupFilesResponse:
    return get_report_group_files(group_id)


@router.get("/groups/{group_id}/files/{file_id}/content")
def get_report_file_content_route(
    group_id: str,
    file_id: str,
    mode: str = Query(default="inline", pattern="^(inline|attachment)$"),
    current_user: CCUser = Depends(require_permission("reports.view")),
) -> Response:
    content, file_name = get_report_file_content(group_id, file_id)

    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{mode}; filename="{file_name}"'},
    )
