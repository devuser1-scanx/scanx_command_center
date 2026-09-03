from __future__ import annotations

from sqlalchemy.orm import Session

from app.repositories.users import list_active_roles
from app.schemas.auth import RoleResponse


def list_roles(db: Session) -> list[RoleResponse]:
    return [
        RoleResponse.model_validate(role)
        for role in list_active_roles(db)
    ]
