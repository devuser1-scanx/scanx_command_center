from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    OAuthTokenResponse,
    RefreshTokenRequest,
    TokenResponse,
)
from app.services.auth import (
    build_current_user_response,
    login,
    logout,
    refresh_tokens,
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