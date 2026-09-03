from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.core.cookies import (
    REFRESH_TOKEN_COOKIE_NAME,
    clear_auth_cookies,
    get_refresh_token_from_cookie,
    require_csrf_token,
    set_auth_cookies,
)
from app.core.security import generate_secure_token
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.auth import (
    ChangePasswordRequest,
    CurrentUserResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    MessageResponse,
    OAuthTokenResponse,
    ResetPasswordRequest,
    TokenResponse,
)
from app.services.auth import (
    IssuedTokens,
    build_current_user_response,
    change_password,
    login,
    logout,
    refresh_tokens,
    request_password_reset,
    reset_password,
)

router = APIRouter(
    prefix="/auth",
)


def _parse_ip(value: str | None) -> str | None:
    """Returns value if it's a syntactically valid IP address, else None.

    Guards CCLoginAudit.ip_address (a Postgres INET column) against a
    malformed X-Forwarded-For header or a non-IP test-client peer address
    crashing the request with a DB type error.
    """
    if not value:
        return None

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None

    return value


def get_client_ip(request: Request) -> str | None:
    # NOTE: X-Forwarded-For is client-supplied and not verified against a
    # trusted-proxy allowlist here, so it can still be spoofed by a direct
    # caller - only its *format* is validated. Restricting this to a known
    # reverse-proxy hop needs the deployment's actual trusted-proxy IP(s),
    # which aren't available in this codebase.
    forwarded_for = request.headers.get("x-forwarded-for")

    if forwarded_for:
        candidate = _parse_ip(forwarded_for.split(",")[0].strip())

        if candidate:
            return candidate

    if request.client:
        return _parse_ip(request.client.host)

    return None


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _respond_with_tokens(response: Response, issued: IssuedTokens) -> TokenResponse:
    """Sets the refresh/CSRF cookies on `response` and returns the public,
    cookie-free response body.
    """
    set_auth_cookies(
        response,
        refresh_token=issued.refresh_token,
        csrf_token=generate_secure_token(),
    )

    return TokenResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=issued.user,
    )


@router.post(
    "/token",
    response_model=OAuthTokenResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def swagger_token_login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> OAuthTokenResponse:
    token_response = login(
        db,
        email=form_data.username,
        password=form_data.password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return OAuthTokenResponse(
        access_token=token_response.access_token,
        token_type="bearer",
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
)
def login_user(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    issued = login(
        db,
        email=str(payload.email),
        password=payload.password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return _respond_with_tokens(response, issued)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf_token)],
)
def refresh_access_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    refresh_token = get_refresh_token_from_cookie(request)

    issued = refresh_tokens(
        db,
        refresh_token=refresh_token,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return _respond_with_tokens(response, issued)


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf_token)],
)
def logout_user(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> MessageResponse:
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)

    if refresh_token:
        logout(
            db,
            refresh_token=refresh_token,
        )

    clear_auth_cookies(response)

    return MessageResponse(
        message="Logged out successfully.",
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    status_code=status.HTTP_200_OK,
)
def get_my_profile(
    current_user: CCUser = Depends(get_current_user),
) -> CurrentUserResponse:
    return build_current_user_response(current_user)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
def change_my_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: CCUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    change_password(
        db,
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        confirm_new_password=payload.confirm_new_password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return MessageResponse(
        message=("Password changed successfully. Please log in again using the new password.")
    )


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    request_password_reset(
        db,
        email=str(payload.email),
    )

    return ForgotPasswordResponse(
        message=(
            "If an active account exists for this email, "
            "password reset instructions have been sent."
        )
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
def reset_user_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> MessageResponse:
    reset_password(
        db,
        raw_token=payload.token,
        new_password=payload.new_password,
        confirm_new_password=payload.confirm_new_password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return MessageResponse(
        message=("Password reset successfully. You may now log in using the new password.")
    )
