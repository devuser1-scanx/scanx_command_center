from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import (
    CCPermission,
    CCRole,
    CCRolePermission,
    CCSession,
    CCUser,
    CCUserClinicAccess,
    CCUserRole,
)


def get_user_by_email(
    db: Session,
    email: str,
) -> CCUser | None:
    statement = (
        select(CCUser)
        .where(CCUser.email == email.strip().lower())
        .options(
            selectinload(CCUser.roles)
            .selectinload(CCUserRole.role)
            .selectinload(CCRole.permissions)
            .selectinload(CCRolePermission.permission),
            selectinload(CCUser.clinic_access),
        )
    )

    return db.scalar(statement)


def get_user_by_id(
    db: Session,
    user_id: int,
) -> CCUser | None:
    statement = (
        select(CCUser)
        .where(CCUser.id == user_id)
        .options(
            selectinload(CCUser.roles)
            .selectinload(CCUserRole.role)
            .selectinload(CCRole.permissions)
            .selectinload(CCRolePermission.permission),
            selectinload(CCUser.clinic_access),
        )
    )

    return db.scalar(statement)


def get_session_by_refresh_token_hash(
    db: Session,
    refresh_token_hash: str,
) -> CCSession | None:
    statement = (
        select(CCSession)
        .where(
            CCSession.refresh_token_hash == refresh_token_hash,
        )
        .options(
            selectinload(CCSession.user)
            .selectinload(CCUser.roles)
            .selectinload(CCUserRole.role)
            .selectinload(CCRole.permissions)
            .selectinload(CCRolePermission.permission),
            selectinload(CCSession.user)
            .selectinload(CCUser.clinic_access),
        )
    )

    return db.scalar(statement)


def create_session(
    db: Session,
    *,
    user_id: int,
    refresh_token_hash: str,
    expires_at: datetime,
    user_agent: str | None,
    ip_address: str | None,
) -> CCSession:
    session = CCSession(
        user_id=user_id,
        refresh_token_hash=refresh_token_hash,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    db.add(session)
    db.flush()

    return session


def revoke_session(
    db: Session,
    *,
    session: CCSession,
    revoked_at: datetime,
) -> None:
    session.revoked_at = revoked_at
    db.add(session)


def collect_role_codes(user: CCUser) -> list[str]:
    return sorted(
        {
            user_role.role.code
            for user_role in user.roles
            if user_role.role.is_active
        }
    )


def collect_permission_codes(user: CCUser) -> list[str]:
    permission_codes: set[str] = set()

    for user_role in user.roles:
        role = user_role.role

        if not role.is_active:
            continue

        for role_permission in role.permissions:
            permission: CCPermission = role_permission.permission

            if permission.is_active:
                permission_codes.add(permission.code)

    return sorted(permission_codes)


def collect_clinic_access(
    user: CCUser,
) -> list[CCUserClinicAccess]:
    return sorted(
        user.clinic_access,
        key=lambda access: (
            not access.is_primary,
            access.clinic_id,
        ),
    )