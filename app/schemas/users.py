from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreateRequest(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    temporary_password: str = Field(min_length=12, max_length=200)

    role_codes: list[str] = Field(default_factory=list)
    clinic_ids: list[int] = Field(default_factory=list)
    primary_clinic_id: int | None = None


class UserUpdateRequest(BaseModel):
    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    phone: str | None = Field(
        default=None,
        max_length=30,
    )


class UserStatusRequest(BaseModel):
    is_active: bool


class UserRoleAssignmentRequest(BaseModel):
    role_codes: list[str] = Field(min_length=1)


class UserClinicAssignmentRequest(BaseModel):
    clinic_ids: list[int]
    primary_clinic_id: int | None = None


class AdminPasswordResetRequest(BaseModel):
    temporary_password: str = Field(
        min_length=12,
        max_length=200,
    )


class UserRoleItem(BaseModel):
    id: int
    code: str
    name: str


class UserClinicItem(BaseModel):
    clinic_id: int
    is_primary: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None

    is_active: bool
    is_email_verified: bool
    must_change_password: bool

    failed_login_attempts: int
    locked_until: datetime | None
    last_login_at: datetime | None
    password_changed_at: datetime | None

    created_at: datetime
    updated_at: datetime

    roles: list[UserRoleItem]
    clinic_access: list[UserClinicItem]


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int
