from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CallLogResponse(BaseModel):
    """One call, with the patient it was matched to (when there is one)."""

    id: int
    appointment_id: str | None = None
    staff_phone: str | None = None
    patient_number: str | None = None
    direction: str
    call_sid: str | None = None
    status: str | None = None
    duration: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    first_name: str | None = None
    last_name: str | None = None
    patient_name: str | None = None
    display_name: str
    is_known_patient: bool
    is_missed: bool

    email: str | None = None
    appointment_type: str | None = None
    clinic_id: int | None = None
    location: str | None = None
    calendar: str | None = None
    clinic_name: str | None = None


class CallsListResponse(BaseModel):
    count: int
    results: list[CallLogResponse]


class CallsSummaryResponse(BaseModel):
    start_date: str
    end_date: str
    total_calls: int
    outgoing_calls: int
    incoming_calls: int
    missed_calls: int
    completed_calls: int
    failed_or_busy_calls: int
    no_answer_calls: int
    average_duration_seconds: int
    recent_calls: list[CallLogResponse]
    missed_calls_list: list[CallLogResponse]


class CallPatientResponse(BaseModel):
    appointment_id: str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    appointment_type: str | None = None
    category: str | None = None
    appointment_datetime: str | None = None
    date: str | None = None
    time: str | None = None
    clinic_id: int | None = None
    location: str | None = None
    calendar: str | None = None
    clinic_name: str | None = None
    clinic_city: str | None = None


class CallPatientSearchResponse(BaseModel):
    query: str
    count: int
    results: list[CallPatientResponse]


class CallPatientHistoryResponse(BaseModel):
    phone: str
    history: list[CallLogResponse]


class DialRequest(BaseModel):
    # What the person typed or the patient's number as stored: validated and
    # turned into E.164 by the server.
    number: str = Field(min_length=3, max_length=40)
    # The appointment the call is about, when it is placed from a patient.
    appointment_id: str | None = Field(default=None, max_length=50)


class DialResponse(BaseModel):
    """What the browser needs to start the call: a short-lived token, the call id
    it sends Twilio as `CcCallId` (so the call can be tied to this record), and
    the number to dial, already validated and in E.164."""

    call_id: int
    token: str
    to: str


class EndRequest(BaseModel):
    """The browser reporting its call over."""

    # False: the call never reached Twilio (microphone refused, no connection).
    reached_twilio: bool
    # Seconds the call lasted as the browser saw it (0 when it never connected).
    duration_s: int = Field(default=0, ge=0, le=86_400)


class NoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


class DispositionRequest(BaseModel):
    disposition: str = Field(min_length=1, max_length=100)


class CreatedResponse(BaseModel):
    id: int
