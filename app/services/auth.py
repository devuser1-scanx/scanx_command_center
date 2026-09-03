from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_utc,
    generate_secure_token,
    hash_password,
    hash_token,
    utc_now,
    validate_password_strength,
    verify_password,
)
from app.integrations.gmail_client import GmailApiError, send_email
from app.models.auth import (
    CCLoginAudit,
    CCUser,
    CCUserActivityAudit,
)
from app.repositories.auth import (
    collect_clinic_access,
    collect_permission_codes,
    collect_role_codes,
    create_password_reset_token,
    create_session,
    get_password_reset_token,
    get_session_by_refresh_token_hash,
    get_user_by_email,
    get_user_by_id,
    invalidate_unused_password_reset_tokens,
    revoke_all_user_sessions,
    revoke_session,
)
from app.schemas.auth import (
    ClinicAccessResponse,
    CurrentUserResponse,
    RoleResponse,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedTokens:
    """Internal carrier for a freshly issued token pair.

    Deliberately not the TokenResponse schema: the refresh token here is
    only ever meant to be set as an HttpOnly cookie by the route layer,
    never serialized into a JSON response body.
    """

    access_token: str
    refresh_token: str
    expires_in: int
    user: CurrentUserResponse


INVALID_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password.",
    headers={"WWW-Authenticate": "Bearer"},
)

# A precomputed hash with no corresponding real user, verified against on
# the user-not-found login path so that branch costs roughly the same
# Argon2 work as the wrong-password branch - otherwise the two are
# distinguishable by response timing, a minor user-enumeration side
# channel despite both returning the same generic error.
_DUMMY_PASSWORD_HASH = hash_password("Dummy-Verification-Hash-Not-A-Real-Password-1!")

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
        # Spends the same Argon2-verification time a real wrong-password
        # attempt would, so this branch isn't distinguishable by timing.
        verify_password(password, _DUMMY_PASSWORD_HASH)

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
        # A distinct status/message here would let a caller distinguish
        # "this account exists but is deactivated" from "wrong password" -
        # a minor account-enumeration leak. The real reason is still
        # captured in CCLoginAudit.failure_reason for support/admin use;
        # the HTTP response stays generic like every other failure mode.
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

        raise INVALID_CREDENTIALS_ERROR

    if user.locked_until and ensure_utc(user.locked_until) > now:
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
            user.locked_until = now + timedelta(minutes=settings.auth_account_lock_minutes)
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
) -> IssuedTokens:
    access_token = create_access_token(
        subject=str(user.id),
        additional_claims={
            "roles": collect_role_codes(user),
            "permissions": collect_permission_codes(user),
        },
    )

    refresh_token = create_refresh_token(subject=str(user.id))
    refresh_expires_at = utc_now() + timedelta(days=settings.jwt_refresh_token_expire_days)

    create_session(
        db,
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        expires_at=refresh_expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    db.commit()

    return IssuedTokens(
        access_token=access_token,
        refresh_token=refresh_token,
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
) -> IssuedTokens:
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
) -> IssuedTokens:
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

    if session.revoked_at is not None or ensure_utc(session.expires_at) <= now:
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


def change_password(
    db: Session,
    *,
    user: CCUser,
    current_password: str,
    new_password: str,
    confirm_new_password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    if new_password != confirm_new_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password and confirmation do not match.",
        )

    if not verify_password(
        current_password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    if verify_password(
        new_password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("New password must be different from the current password."),
        )

    try:
        validate_password_strength(new_password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    now = utc_now()

    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None

    revoke_all_user_sessions(
        db,
        user_id=user.id,
        revoked_at=now,
    )

    db.add(user)

    record_user_activity(
        db,
        actor_user_id=user.id,
        target_user_id=user.id,
        action="auth.password_changed",
        resource_type="cc_user",
        resource_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()


def _send_password_reset_email(
    user: CCUser,
    raw_token: str,
) -> None:
    reset_link = f"{settings.frontend_base_url.rstrip('/')}/reset-password?token={raw_token}"

    html_body = f"""
<p>Hi {user.first_name},</p>
<p>We received a request to reset your ScanX Command Center password.</p>
<p><a href="{reset_link}">Reset your password</a></p>
<p>This link expires in {settings.password_reset_token_expire_minutes} minutes.
If you didn't request this, you can safely ignore this email.</p>
""".strip()

    try:
        send_email(
            to=[user.email],
            cc=[],
            bcc=[],
            subject="Reset your ScanX Command Center password",
            html_body=html_body,
            attachments=[],
        )
    except (GmailApiError, RuntimeError):
        # The forgot-password endpoint always returns the same generic
        # message regardless of outcome (no account-enumeration signal) -
        # a delivery failure here must not surface to the caller, but must
        # still be observable operationally.
        logger.exception("Failed to send password reset email to user_id=%s", user.id)


def request_password_reset(
    db: Session,
    *,
    email: str,
) -> None:
    normalized_email = email.strip().lower()
    user = get_user_by_email(db, normalized_email)

    # Do not reveal whether an account exists.
    if user is None or not user.is_active:
        return None

    now = utc_now()

    invalidate_unused_password_reset_tokens(
        db,
        user_id=user.id,
        used_at=now,
    )

    raw_token = generate_secure_token()

    create_password_reset_token(
        db,
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=now + timedelta(minutes=settings.password_reset_token_expire_minutes),
    )

    db.commit()

    _send_password_reset_email(user, raw_token)

    return None


def reset_password(
    db: Session,
    *,
    raw_token: str,
    new_password: str,
    confirm_new_password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    if new_password != confirm_new_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password and confirmation do not match.",
        )

    try:
        validate_password_strength(new_password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    reset_token = get_password_reset_token(
        db,
        token_hash=hash_token(raw_token),
    )

    if reset_token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    now = utc_now()

    if reset_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset token has already been used.",
        )

    if ensure_utc(reset_token.expires_at) <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset token has expired.",
        )

    user = reset_token.user

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    if verify_password(
        new_password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("New password must be different from the existing password."),
        )

    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None

    reset_token.used_at = now

    revoke_all_user_sessions(
        db,
        user_id=user.id,
        revoked_at=now,
    )

    db.add(user)
    db.add(reset_token)

    record_user_activity(
        db,
        actor_user_id=user.id,
        target_user_id=user.id,
        action="auth.password_reset",
        resource_type="cc_user",
        resource_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()


def record_user_activity(
    db: Session,
    *,
    actor_user_id: int | None,
    target_user_id: int | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    clinic_id: int | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    db.add(
        CCUserActivityAudit(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            clinic_id=clinic_id,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
