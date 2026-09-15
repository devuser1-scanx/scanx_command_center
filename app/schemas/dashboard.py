from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

Tone = Literal["purple", "green", "pink", "yellow", "red", "teal", "blue"]


class ClinicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str | None
    timezone: str | None


class TimelineAppointmentResponse(BaseModel):
    id: str
    patient: str
    exam: str
    time: str
    status: str
    tone: Tone
    paid: bool
    duration_minutes: int


class DashboardTimelineResponse(BaseModel):
    clinic_id: int | None
    date: date
    appointments: list[TimelineAppointmentResponse]


class DashboardSummaryResponse(BaseModel):
    date: date
    today: int
    confirmed: int
    checked_in: int
    late: int
    report_pending: int
