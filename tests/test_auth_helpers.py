from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies.auth import (
    get_current_user,
    require_permission,
    require_role,
)
from app.api.v1.routes.auth import (
    get_client_ip,
    get_user_agent,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
)
from app.models.auth import (
    CCPermission,
    CCRole,
    CCRolePermission,
    CCUser,
    CCUserClinicAccess,
    CCUserRole,
)
from app.repositories.auth import (
    collect_clinic_access,
    collect_permission_codes,
    collect_role_codes,
)
from app.services.auth import build_current_user_response


class FakeDb:
    pass


def make_user() -> CCUser:
    user = CCUser(
        id=1,
        email="admin@example.com",
        first_name="Ada",
        last_name="Lovelace",
        phone="555-0100",
        password_hash="hash",
        is_active=True,
        is_email_verified=True,
        must_change_password=False,
        failed_login_attempts=0,
        last_login_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
    )

    users_view = CCPermission(
        id=1,
        code="users.view",
        module="users",
        name="View Users",
        is_active=True,
    )

    inactive_permission = CCPermission(
        id=2,
        code="users.delete",
        module="users",
        name="Delete Users",
        is_active=False,
    )

    admin_role = CCRole(
        id=1,
        code="admin",
        name="Admin",
        is_active=True,
    )

    inactive_role = CCRole(
        id=2,
        code="disabled",
        name="Disabled",
        is_active=False,
    )

    admin_role.permissions = [
        CCRolePermission(
            role=admin_role,
            permission=users_view,
        ),
        CCRolePermission(
            role=admin_role,
            permission=inactive_permission,
        ),
    ]

    inactive_role.permissions = [
        CCRolePermission(
            role=inactive_role,
            permission=users_view,
        ),
    ]

    user.roles = [
        CCUserRole(
            user=user,
            role=admin_role,
        ),
        CCUserRole(
            user=user,
            role=inactive_role,
        ),
    ]

    user.clinic_access = [
        CCUserClinicAccess(
            user=user,
            clinic_id=20,
            is_primary=False,
        ),
        CCUserClinicAccess(
            user=user,
            clinic_id=10,
            is_primary=True,
        ),
    ]

    return user


def make_request(
    headers: list[tuple[bytes, bytes]],
    host: str = "127.0.0.1",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (host, 1234),
        }
    )


def test_request_helpers_prefer_forwarded_ip_and_read_user_agent() -> None:
    request = make_request(
        [
            (
                b"x-forwarded-for",
                b"203.0.113.10, 10.0.0.1",
            ),
            (
                b"user-agent",
                b"pytest",
            ),
        ]
    )

    assert get_client_ip(request) == "203.0.113.10"
    assert get_user_agent(request) == "pytest"


def test_request_helpers_fall_back_to_client_host() -> None:
    request = make_request([])

    assert get_client_ip(request) == "127.0.0.1"
    assert get_user_agent(request) is None


def test_collectors_ignore_inactive_roles_and_permissions() -> None:
    user = make_user()

    assert collect_role_codes(user) == ["admin"]
    assert collect_permission_codes(user) == ["users.view"]

    clinic_ids = [access.clinic_id for access in collect_clinic_access(user)]

    assert clinic_ids == [10, 20]


def test_build_current_user_response_filters_inactive_access() -> None:
    response = build_current_user_response(make_user())

    assert response.email == "admin@example.com"
    assert [role.code for role in response.roles] == ["admin"]

    assert response.permissions == ["users.view"]

    assert [access.clinic_id for access in response.clinic_access] == [10, 20]


def test_get_current_user_returns_active_access_token_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user()

    def fake_get_user_by_id(
        db: FakeDb,
        user_id: int,
    ) -> CCUser | None:
        assert isinstance(db, FakeDb)
        assert user_id == user.id

        return user

    monkeypatch.setattr(
        "app.api.dependencies.auth.get_user_by_id",
        fake_get_user_by_id,
    )

    token = create_access_token(subject=str(user.id))

    assert (
        get_current_user(
            token=token,
            db=FakeDb(),
        )
        is user
    )


def test_get_current_user_rejects_refresh_token() -> None:
    token = create_refresh_token(
        subject="1",
        session_id=1,
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(
            token=token,
            db=FakeDb(),
        )

    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_inactive_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user()
    user.is_active = False

    monkeypatch.setattr(
        "app.api.dependencies.auth.get_user_by_id",
        lambda db, user_id: user,
    )

    token = create_access_token(subject=str(user.id))

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(
            token=token,
            db=FakeDb(),
        )

    assert exc_info.value.status_code == 403


def test_role_and_permission_dependencies_allow_matching_user() -> None:
    user = make_user()

    assert require_role("admin")(current_user=user) is user

    assert require_permission("users.view")(current_user=user) is user


def test_role_and_permission_dependencies_reject_missing_access() -> None:
    user = make_user()

    with pytest.raises(HTTPException) as role_error:
        require_role("front_desk")(current_user=user)

    with pytest.raises(HTTPException) as permission_error:
        require_permission("users.create")(current_user=user)

    assert role_error.value.status_code == 403
    assert permission_error.value.status_code == 403
