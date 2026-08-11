from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.db.prod_session import get_prod_db
from app.models.auth import CCUser
from app.schemas.patients import PatientProfileResponse, PatientSearchResponse
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
