from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.auth import CCUser
from app.repositories.auth import (
    collect_permission_codes,
    collect_role_codes,
    get_user_by_id,
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/token",
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CCUser:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise credentials_error from exc

    if payload.get("type") != "access":
        raise credentials_error

    subject = payload.get("sub")

    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise credentials_error from exc

    user = get_user_by_id(db, user_id)

    if user is None:
        raise credentials_error

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    return user


def require_permission(
    permission_code: str,
) -> Callable[[CCUser], CCUser]:
    def permission_dependency(
        current_user: CCUser = Depends(get_current_user),
    ) -> CCUser:
        permissions = collect_permission_codes(current_user)

        if permission_code not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(f"Permission '{permission_code}' is required to perform this action."),
            )

        return current_user

    return permission_dependency


def require_role(
    role_code: str,
) -> Callable[[CCUser], CCUser]:
    def role_dependency(
        current_user: CCUser = Depends(get_current_user),
    ) -> CCUser:
        roles = collect_role_codes(current_user)

        if role_code not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role_code}' is required.",
            )

        return current_user

    return role_dependency


def require_admin(
    current_user: CCUser = Depends(require_role("admin")),
) -> CCUser:
    return current_user
