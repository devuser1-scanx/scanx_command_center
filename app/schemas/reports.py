from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ReportSource = Literal["tricefy", "fibroscan"]


class ReportGroupItem(BaseModel):
    id: str
    source: ReportSource
    name: str
    file_count: int
    updated_at: datetime | None


class ReportGroupListResponse(BaseModel):
    items: list[ReportGroupItem]
    total: int
    page: int
    page_size: int


class ReportFileItem(BaseModel):
    id: str
    file_name: str
    size_bytes: int
    updated_at: datetime | None


class ReportGroupFilesResponse(BaseModel):
    group_id: str
    group_name: str
    source: ReportSource
    files: list[ReportFileItem]
