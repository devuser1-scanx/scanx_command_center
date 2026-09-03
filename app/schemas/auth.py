from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import validate_password_strength


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)
    confirm_new_password: str = Field(min_length=12, max_length=200)

    # Defense-in-depth: the service layer already calls
    # validate_password_strength() before hashing, so this schema-level
    # check can never actually let a weak password through today - it
    # exists so that stays true even if a future code path calls
    # hash_password() some other way that skips the service function.
    @field_validator("new_password")
    @classmethod
    def _check_new_password_strength(cls, value: str) -> str:
        validate_password_strength(value)
        return value


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=500)
    new_password: str = Field(min_length=12, max_length=200)
    confirm_new_password: str = Field(min_length=12, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _check_new_password_strength(cls, value: str) -> str:
        validate_password_strength(value)
        return value


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    module: str
    name: str


class ClinicAccessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clinic_id: int
    is_primary: bool


class CurrentUserResponse(BaseModel):
    id: int
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None

    roles: list[RoleResponse]
    permissions: list[str]
    clinic_access: list[ClinicAccessResponse]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUserResponse


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str


class ForgotPasswordResponse(BaseModel):
    message: str
