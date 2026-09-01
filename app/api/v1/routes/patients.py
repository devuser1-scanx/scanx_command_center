from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.db.prod_session import get_prod_db
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.fax import FaxReportLookupResponse, SendFaxResponse
from app.schemas.mail import SendMailResponse
from app.schemas.patients import PatientProfileResponse, PatientSearchResponse
from app.services.fax import lookup_patient_report, send_patient_fax
from app.services.mail import send_patient_mail
from app.services.patients import get_patient_profile, search_patients

router = APIRouter(prefix="/patients")


@router.get("/search", response_model=PatientSearchResponse)
def search_patients_route(
    q: str | None = Query(default=None),
    day: date | None = Query(default=None, alias="date"),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("patients.search")),
) -> PatientSearchResponse:
    return search_patients(
        prod_db,
        query=q,
        day=day or date.today(),
    )


@router.get("/{appointment_id}", response_model=PatientProfileResponse)
def get_patient_profile_route(
    appointment_id: str,
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("patients.view")),
) -> PatientProfileResponse:
    return get_patient_profile(prod_db, appointment_id)


@router.get("/{appointment_id}/fax/report", response_model=FaxReportLookupResponse)
def lookup_patient_report_route(
    appointment_id: str,
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("patients.fax")),
) -> FaxReportLookupResponse:
    return lookup_patient_report(prod_db, appointment_id)


@router.post("/{appointment_id}/fax", response_model=SendFaxResponse)
def send_fax_route(
    appointment_id: str,
    destination_number: str = Form(...),
    subject: str | None = Form(default=None),
    include_report: bool = Form(True),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("patients.fax")),
) -> SendFaxResponse:
    return send_patient_fax(
        db,
        prod_db,
        appointment_id=appointment_id,
        destination_number=destination_number,
        subject=subject,
        include_report=include_report,
        uploaded_files=files,
        actor=current_user,
    )


@router.post("/{appointment_id}/mail", response_model=SendMailResponse)
def send_mail_route(
    appointment_id: str,
    to: str = Form(...),
    cc: str | None = Form(default=None),
    bcc: str | None = Form(default=None),
    subject: str = Form(...),
    body_html: str = Form(...),
    include_report: bool = Form(True),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("patients.mail")),
) -> SendMailResponse:
    return send_patient_mail(
        db,
        prod_db,
        appointment_id=appointment_id,
        to_addresses=to,
        cc_addresses=cc,
        bcc_addresses=bcc,
        subject=subject,
        body_html=body_html,
        include_report=include_report,
        uploaded_files=files,
        actor=current_user,
    )
