from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.db.prod_session import get_prod_db
from app.models.auth import CCUser
from app.repositories.dashboard import list_clinics
from app.schemas.dashboard import ClinicResponse

router = APIRouter(prefix="/clinics")


@router.get("", response_model=list[ClinicResponse])
def list_command_center_clinics(
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("clinics.view")),
) -> list[ClinicResponse]:
    clinics = list_clinics(prod_db)

    return [ClinicResponse.model_validate(clinic) for clinic in clinics]
