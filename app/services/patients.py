from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.production import Appointment
from app.repositories.dashboard import list_clinics
from app.repositories.patients import (
    get_appointment_by_appointment_id,
    get_checkin,
    get_patient_intake,
    list_appointments_for_phone,
    list_call_logs,
    list_form_status,
    list_form_tracking,
    list_messages,
    list_reports,
    list_uploads,
    search_patients as search_patients_repository,
)
from app.schemas.patients import (
    CallLogItem,
    CheckinInfo,
    FormStatusItem,
    MessageItem,
    PatientIntake,
    PatientProfileResponse,
    PatientSearchResponse,
    PatientSummaryResponse,
    PaymentInfo,
    ReportItem,
    UploadItem,
    VisitSummary,
)
from app.services.dashboard import build_local_time, build_patient_name, derive_status_and_tone


def _group_key(appointment: Appointment) -> str:
    """Groups by phone when present; falls back to one group per appointment."""
    return appointment.phone or f"__appointment_{appointment.appointment_id}"


def _clinic_names(prod_db: Session) -> dict[int, str]:
    return {clinic.id: clinic.name for clinic in list_clinics(prod_db)}


def _clinic_timezones(prod_db: Session) -> dict[int, str | None]:
    return {clinic.id: clinic.timezone for clinic in list_clinics(prod_db)}


def _build_visit_summary(
    appointment: Appointment,
    *,
    clinic_names: dict[int, str],
    clinic_timezones: dict[int, str | None],
) -> VisitSummary:
    status_label, tone = derive_status_and_tone(appointment)
    clinic_timezone = clinic_timezones.get(appointment.clinic_id)

    return VisitSummary(
        appointment_id=appointment.appointment_id,
        date=(
            appointment.appointment_datetime.date().isoformat()
            if appointment.appointment_datetime
            else ""
        ),
        time=build_local_time(appointment, clinic_timezone),
        clinic_name=clinic_names.get(appointment.clinic_id, "Unknown Clinic"),
        exam=appointment.appointment_type or appointment.category or "Exam",
        status=status_label,
        tone=tone,
        price=float(appointment.price) if appointment.price is not None else None,
        amount_paid=(
            float(appointment.amount_paid)
            if appointment.amount_paid is not None
            else None
        ),
        paid=appointment.paid,
    )


def search_patients(
    prod_db: Session,
    *,
    query: str | None,
    day: date | None,
) -> PatientSearchResponse:
    appointments = search_patients_repository(prod_db, query=query, day=day)

    clinic_names = _clinic_names(prod_db)
    clinic_timezones = _clinic_timezones(prod_db)

    groups: dict[str, list[Appointment]] = {}

    for appointment in appointments:
        groups.setdefault(_group_key(appointment), []).append(appointment)

    summaries: list[PatientSummaryResponse] = []

    for group in groups.values():
        latest = max(
            group,
            key=lambda appt: appt.appointment_datetime or appt.date or date.min,
        )
        status_label, tone = derive_status_and_tone(latest)
        clinic_timezone = clinic_timezones.get(latest.clinic_id)

        summaries.append(
            PatientSummaryResponse(
                phone=latest.phone,
                patient=build_patient_name(latest),
                appointment_count=len(group),
                latest_appointment_id=latest.appointment_id,
                latest_clinic_name=clinic_names.get(
                    latest.clinic_id, "Unknown Clinic"
                ),
                latest_exam=latest.appointment_type or latest.category or "Exam",
                latest_time=build_local_time(latest, clinic_timezone),
                latest_status=status_label,
                latest_tone=tone,
            )
        )

    summaries.sort(key=lambda item: item.patient.lower())

    return PatientSearchResponse(items=summaries)


def get_patient_profile(
    prod_db: Session,
    appointment_id: str,
) -> PatientProfileResponse:
    appointment = get_appointment_by_appointment_id(prod_db, appointment_id)

    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found.",
        )

    clinic_names = _clinic_names(prod_db)
    clinic_timezones = _clinic_timezones(prod_db)

    visit_appointments = (
        list_appointments_for_phone(prod_db, appointment.phone)
        if appointment.phone
        else [appointment]
    )

    if appointment.appointment_id not in {
        visit.appointment_id for visit in visit_appointments
    }:
        visit_appointments.append(appointment)

    visits = [
        _build_visit_summary(
            visit,
            clinic_names=clinic_names,
            clinic_timezones=clinic_timezones,
        )
        for visit in visit_appointments
    ]

    intake_row = get_patient_intake(prod_db, appointment_id)
    intake = (
        PatientIntake(
            dob=intake_row.dob,
            gender=intake_row.gender,
            height=intake_row.height,
            weight=intake_row.weight,
            reason=intake_row.reason,
            reason_other=intake_row.reason_other,
            exam_date=intake_row.exam_date,
            physician_name=intake_row.physician_name,
            physician_email=intake_row.physician_email,
            physician_fax_no=intake_row.physician_fax_no,
            doctor_order_value=intake_row.doctor_order_value,
            blood_pressure_sys=intake_row.blood_pressure_sys,
            blood_pressure_dia=intake_row.blood_pressure_dia,
            sms_consent_value=intake_row.sms_consent_value,
            hippa_response=intake_row.hippa_response,
            accept_terms=intake_row.accept_terms,
        )
        if intake_row
        else None
    )

    checkin_row = get_checkin(prod_db, appointment_id)
    checkin = (
        CheckinInfo(
            status=checkin_row.status,
            checkin_time=checkin_row.checkin_time,
            location=checkin_row.location,
        )
        if checkin_row
        else None
    )

    forms: list[FormStatusItem] = [
        FormStatusItem(
            source="form_status",
            form_type=None,
            status=(
                "Sent"
                if row.sent_status
                else "Not sent"
            ),
            sent_at=row.sent_at,
            submitted_at=None,
        )
        for row in list_form_status(prod_db, appointment_id)
    ] + [
        FormStatusItem(
            source="form_tracking",
            form_type=row.form_type,
            status=row.status,
            sent_at=row.sent_at,
            submitted_at=row.submitted_at,
        )
        for row in list_form_tracking(prod_db, appointment_id)
    ]

    messages = [
        MessageItem(
            direction=row.direction,
            channel=row.channel,
            body=row.body,
            status=row.status,
            timestamp=row.timestamp,
        )
        for row in list_messages(prod_db, appointment_id)
    ]

    call_logs = [
        CallLogItem(
            direction=row.direction,
            status=row.status,
            duration=row.duration,
            created_at=row.created_at,
        )
        for row in list_call_logs(prod_db, appointment_id)
    ]

    reports = [
        ReportItem(
            file_name=row.file_name,
            delivery_link=row.delivery_link,
            link_expires_at=row.link_expires_at,
            accessed=row.accessed,
            created_at=row.created_at,
        )
        for row in list_reports(prod_db, appointment_id)
    ]

    uploads = [
        UploadItem(
            file_name=row.file_name,
            file_type=row.file_type,
            uploaded_at=row.uploaded_at,
            verified=row.verified,
        )
        for row in list_uploads(prod_db, appointment_id)
    ]

    payment = PaymentInfo(
        price=float(appointment.price) if appointment.price is not None else None,
        amount_paid=(
            float(appointment.amount_paid)
            if appointment.amount_paid is not None
            else None
        ),
        paid=appointment.paid,
        payment_link=appointment.payment_link,
    )

    return PatientProfileResponse(
        patient=build_patient_name(appointment),
        phone=appointment.phone,
        email=appointment.email,
        visits=sorted(visits, key=lambda visit: visit.date, reverse=True),
        selected_appointment_id=appointment.appointment_id,
        intake=intake,
        checkin=checkin,
        forms=forms,
        messages=messages,
        call_logs=call_logs,
        reports=reports,
        uploads=uploads,
        payment=payment,
    )
