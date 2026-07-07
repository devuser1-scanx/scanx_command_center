from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.models.auth import (
    CCPasswordResetToken,
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


def create_password_reset_token(
    db: Session,
    *,
    user_id: int,
    token_hash: str,
    expires_at: datetime,
) -> CCPasswordResetToken:
    reset_token = CCPasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    db.add(reset_token)
    db.flush()

    return reset_token


def get_password_reset_token(
    db: Session,
    *,
    token_hash: str,
) -> CCPasswordResetToken | None:
    statement = (
        select(CCPasswordResetToken)
        .where(
            CCPasswordResetToken.token_hash == token_hash,
        )
        .options(
            selectinload(CCPasswordResetToken.user)
        )
    )

    return db.scalar(statement)


def invalidate_unused_password_reset_tokens(
    db: Session,
    *,
    user_id: int,
    used_at: datetime,
) -> None:
    statement = (
        update(CCPasswordResetToken)
        .where(
            CCPasswordResetToken.user_id == user_id,
            CCPasswordResetToken.used_at.is_(None),
        )
        .values(
            used_at=used_at,
        )
    )

    db.execute(statement)


def revoke_all_user_sessions(
    db: Session,
    *,
    user_id: int,
    revoked_at: datetime,
) -> None:
    statement = (
        update(CCSession)
        .where(
            CCSession.user_id == user_id,
            CCSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=revoked_at,
        )
    )

    db.execute(statement)