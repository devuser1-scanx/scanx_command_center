from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FaxTransmissionItem(BaseModel):
    id: int
    file_name: str
    status: str
    westfax_job_id: str | None
    error_message: str | None


class SendFaxResponse(BaseModel):
    destination_number: str
    westfax_job_id: str | None
    transmissions: list[FaxTransmissionItem]
    created_at: datetime


class FaxReportLookupResponse(BaseModel):
    found: bool
    file_name: str | None
