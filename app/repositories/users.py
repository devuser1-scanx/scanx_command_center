from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import (
    CCRole,
    CCUser,
    CCUserClinicAccess,
    CCUserRole,
)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_query_options():
    return (
        selectinload(CCUser.roles).selectinload(CCUserRole.role),
        selectinload(CCUser.clinic_access),
    )


def get_user_by_id(
    db: Session,
    user_id: int,
) -> CCUser | None:
    statement = select(CCUser).where(CCUser.id == user_id).options(*user_query_options())

    return db.scalar(statement)


def get_user_by_email(
    db: Session,
    email: str,
) -> CCUser | None:
    statement = (
        select(CCUser).where(CCUser.email == normalize_email(email)).options(*user_query_options())
    )

    return db.scalar(statement)


def list_users(
    db: Session,
    *,
    limit: int,
    offset: int,
    search: str | None,
    is_active: bool | None,
) -> tuple[list[CCUser], int]:
    filters = []

    if search:
        term = f"%{search.strip()}%"

        filters.append(
            func.concat(
                CCUser.first_name,
                " ",
                CCUser.last_name,
                " ",
                CCUser.email,
            ).ilike(term)
        )

    if is_active is not None:
        filters.append(CCUser.is_active == is_active)

    count_statement = select(func.count(CCUser.id)).where(*filters)

    total = db.scalar(count_statement) or 0

    statement = (
        select(CCUser)
        .where(*filters)
        .options(*user_query_options())
        .order_by(
            CCUser.created_at.desc(),
            CCUser.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    users = list(db.scalars(statement).unique().all())

    return users, total


def get_roles_by_codes(
    db: Session,
    role_codes: list[str],
) -> list[CCRole]:
    normalized_codes = sorted({code.strip().lower() for code in role_codes if code.strip()})

    if not normalized_codes:
        return []

    statement = select(CCRole).where(
        CCRole.code.in_(normalized_codes),
        CCRole.is_active.is_(True),
    )

    return list(db.scalars(statement).all())


def replace_user_roles(
    db: Session,
    *,
    user: CCUser,
    roles: list[CCRole],
    assigned_by_user_id: int,
) -> None:
    user.roles.clear()
    db.flush()

    for role in roles:
        user.roles.append(
            CCUserRole(
                role_id=role.id,
                assigned_by_user_id=assigned_by_user_id,
            )
        )


def replace_user_clinic_access(
    db: Session,
    *,
    user: CCUser,
    clinic_ids: list[int],
    primary_clinic_id: int | None,
    assigned_by_user_id: int,
) -> None:
    user.clinic_access.clear()
    db.flush()

    unique_clinic_ids = sorted(set(clinic_ids))

    for clinic_id in unique_clinic_ids:
        user.clinic_access.append(
            CCUserClinicAccess(
                clinic_id=clinic_id,
                is_primary=(primary_clinic_id == clinic_id),
                assigned_by_user_id=assigned_by_user_id,
            )
        )
