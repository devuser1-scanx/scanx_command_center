from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

Tone = Literal["orange", "blue", "green", "red", "purple"]


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
