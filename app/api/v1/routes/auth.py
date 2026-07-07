from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.auth import (
    ChangePasswordRequest,
    CurrentUserResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    OAuthTokenResponse,
    RefreshTokenRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.services.auth import (
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


def get_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client:
        return request.client.host

    return None


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


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
    db: Session = Depends(get_db),
) -> TokenResponse:
    return login(
        db,
        email=str(payload.email),
        password=payload.password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
)
def refresh_access_token(
    payload: RefreshTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    return refresh_tokens(
        db,
        refresh_token=payload.refresh_token,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
def logout_user(
    payload: LogoutRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    logout(
        db,
        refresh_token=payload.refresh_token,
    )

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
        message=(
            "Password changed successfully. "
            "Please log in again using the new password."
        )
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
    reset_token = request_password_reset(
        db,
        email=str(payload.email),
    )

    response = ForgotPasswordResponse(
        message=(
            "If an active account exists for this email, "
            "password reset instructions have been generated."
        )
    )

    if settings.app_env.lower() in {
        "local",
        "dev",
        "development",
        "test",
    }:
        response.reset_token = reset_token

    return response


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
        message=(
            "Password reset successfully. "
            "You may now log in using the new password."
        )
    )