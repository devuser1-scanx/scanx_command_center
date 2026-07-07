from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    utc_now,
    verify_password,
)
from app.models.auth import CCLoginAudit, CCUser
from app.repositories.auth import (
    collect_clinic_access,
    collect_permission_codes,
    collect_role_codes,
    create_session,
    get_session_by_refresh_token_hash,
    get_user_by_email,
    get_user_by_id,
    revoke_session,
)
from app.schemas.auth import (
    ClinicAccessResponse,
    CurrentUserResponse,
    RoleResponse,
    TokenResponse,
)

INVALID_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password.",
    headers={"WWW-Authenticate": "Bearer"},
)

INVALID_REFRESH_TOKEN_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired refresh token.",
    headers={"WWW-Authenticate": "Bearer"},
)


def build_current_user_response(
    user: CCUser,
) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        is_active=user.is_active,
        is_email_verified=user.is_email_verified,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        roles=[
            RoleResponse(
                id=user_role.role.id,
                code=user_role.role.code,
                name=user_role.role.name,
            )
            for user_role in user.roles
            if user_role.role.is_active
        ],
        permissions=collect_permission_codes(user),
        clinic_access=[
            ClinicAccessResponse(
                clinic_id=access.clinic_id,
                is_primary=access.is_primary,
            )
            for access in collect_clinic_access(user)
        ],
    )


def record_login_attempt(
    db: Session,
    *,
    email: str,
    success: bool,
    user_id: int | None,
    failure_reason: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    db.add(
        CCLoginAudit(
            user_id=user_id,
            email=email,
            success=success,
            failure_reason=failure_reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )


def authenticate_user(
    db: Session,
    *,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> CCUser:
    normalized_email = email.strip().lower()
    user = get_user_by_email(db, normalized_email)

    if user is None:
        record_login_attempt(
            db,
            email=normalized_email,
            success=False,
            user_id=None,
            failure_reason="user_not_found",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()
        raise INVALID_CREDENTIALS_ERROR

    now = utc_now()

    if not user.is_active:
        record_login_attempt(
            db,
            email=normalized_email,
            success=False,
            user_id=user.id,
            failure_reason="inactive_user",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    if user.locked_until and user.locked_until > now:
        record_login_attempt(
            db,
            email=normalized_email,
            success=False,
            user_id=user.id,
            failure_reason="account_locked",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="User account is temporarily locked.",
        )

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1

        if user.failed_login_attempts >= settings.auth_max_failed_login_attempts:
            user.locked_until = now + timedelta(
                minutes=settings.auth_account_lock_minutes
            )
            user.failed_login_attempts = 0

        record_login_attempt(
            db,
            email=normalized_email,
            success=False,
            user_id=user.id,
            failure_reason="invalid_password",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()
        raise INVALID_CREDENTIALS_ERROR

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now

    record_login_attempt(
        db,
        email=normalized_email,
        success=True,
        user_id=user.id,
        failure_reason=None,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()
    db.refresh(user)

    refreshed_user = get_user_by_id(db, user.id)

    if refreshed_user is None:
        raise INVALID_CREDENTIALS_ERROR

    return refreshed_user


def issue_tokens(
    db: Session,
    *,
    user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenResponse:
    access_token = create_access_token(
        subject=str(user.id),
        additional_claims={
            "roles": collect_role_codes(user),
            "permissions": collect_permission_codes(user),
        },
    )

    temporary_refresh_token = create_refresh_token(
        subject=str(user.id),
        session_id=0,
    )

    refresh_expires_at = utc_now() + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )

    session = create_session(
        db,
        user_id=user.id,
        refresh_token_hash=hash_token(temporary_refresh_token),
        expires_at=refresh_expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    final_refresh_token = create_refresh_token(
        subject=str(user.id),
        session_id=session.id,
    )

    session.refresh_token_hash = hash_token(final_refresh_token)

    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=final_refresh_token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=build_current_user_response(user),
    )


def login(
    db: Session,
    *,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenResponse:
    user = authenticate_user(
        db,
        email=email,
        password=password,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return issue_tokens(
        db,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def refresh_tokens(
    db: Session,
    *,
    refresh_token: str,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenResponse:
    try:
        payload = decode_token(refresh_token)
    except ValueError as exc:
        raise INVALID_REFRESH_TOKEN_ERROR from exc

    if payload.get("type") != "refresh":
        raise INVALID_REFRESH_TOKEN_ERROR

    session = get_session_by_refresh_token_hash(
        db,
        hash_token(refresh_token),
    )

    if session is None:
        raise INVALID_REFRESH_TOKEN_ERROR

    now = utc_now()

    if session.revoked_at is not None or session.expires_at <= now:
        raise INVALID_REFRESH_TOKEN_ERROR

    if not session.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    revoke_session(
        db,
        session=session,
        revoked_at=now,
    )
    db.commit()

    return issue_tokens(
        db,
        user=session.user,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def logout(
    db: Session,
    *,
    refresh_token: str,
) -> None:
    session = get_session_by_refresh_token_hash(
        db,
        hash_token(refresh_token),
    )

    if session is None:
        return

    if session.revoked_at is None:
        revoke_session(
            db,
            session=session,
            revoked_at=utc_now(),
        )
        db.commit()