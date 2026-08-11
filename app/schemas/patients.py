from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.dashboard import Tone


class PatientSummaryResponse(BaseModel):
    phone: str | None
    patient: str
    appointment_count: int
    latest_appointment_id: str
    latest_clinic_name: str
    latest_exam: str
    latest_time: str
    latest_status: str
    latest_tone: Tone


class PatientSearchResponse(BaseModel):
    items: list[PatientSummaryResponse]


class VisitSummary(BaseModel):
    appointment_id: str
    date: str
    time: str
    clinic_name: str
    exam: str
    status: str
    tone: Tone
    price: float | None
    amount_paid: float | None
    paid: bool | None


class PatientIntake(BaseModel):
    dob: str | None
    gender: str | None
    height: int | None
    weight: int | None
    reason: str | None
    reason_other: str | None
    exam_date: date | None
    physician_name: str | None
    physician_email: str | None
    physician_fax_no: str | None
    doctor_order_value: str | None
    blood_pressure_sys: int | None
    blood_pressure_dia: int | None
    sms_consent_value: str | None
    hippa_response: str | None
    accept_terms: str | None


class CheckinInfo(BaseModel):
    status: str | None
    checkin_time: datetime | None
    location: str | None


class FormStatusItem(BaseModel):
    source: str
    """Which table this row came from - "form_status" or "form_tracking"."""

    form_type: str | None
    status: str | None
    sent_at: datetime | None
    submitted_at: datetime | None


class MessageItem(BaseModel):
    direction: str | None
    channel: str | None
    body: str | None
    status: str | None
    timestamp: datetime | None


class CallLogItem(BaseModel):
    direction: str
    status: str | None
    duration: int | None
    created_at: datetime | None


class ReportItem(BaseModel):
    file_name: str | None
    delivery_link: str | None
    link_expires_at: datetime | None
    accessed: bool | None
    created_at: datetime | None


class UploadItem(BaseModel):
    file_name: str | None
    file_type: str | None
    uploaded_at: datetime | None
    verified: bool | None


class PaymentInfo(BaseModel):
    price: float | None
    amount_paid: float | None
    paid: bool | None
    payment_link: str | None


class PatientProfileResponse(BaseModel):
    patient: str
    phone: str | None
    email: str | None

    visits: list[VisitSummary]
    selected_appointment_id: str

    intake: PatientIntake | None
    checkin: CheckinInfo | None
    forms: list[FormStatusItem]
    messages: list[MessageItem]
    call_logs: list[CallLogItem]
    reports: list[ReportItem]
    uploads: list[UploadItem]
    payment: PaymentInfo
