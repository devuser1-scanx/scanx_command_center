from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.auth import RoleResponse
from app.services.roles import list_roles

router = APIRouter(prefix="/roles")


@router.get("", response_model=list[RoleResponse])
def list_command_center_roles(
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("roles.view")),
) -> list[RoleResponse]:
    del current_user

    return list_roles(db)
