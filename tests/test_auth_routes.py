from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.auth import CCUser
from app.schemas.auth import CurrentUserResponse
from app.services.auth import IssuedTokens
from tests.conftest import create_test_user


class FakeDb:
    pass


def override_db() -> FakeDb:
    return FakeDb()


def test_login_route_passes_payload_and_request_metadata(
    client: TestClient,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_login(
        db: FakeDb,
        *,
        email: str,
        password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> IssuedTokens:
        captured.update(
            db=db,
            email=email,
            password=password,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return IssuedTokens(
            access_token="access",
            refresh_token="refresh",
            expires_in=1800,
            user=CurrentUserResponse(
                id=1,
                email="admin@example.com",
                first_name="Ada",
                last_name="Lovelace",
                phone=None,
                is_active=True,
                must_change_password=False,
                last_login_at=None,
                roles=[],
                permissions=[],
                clinic_access=[],
            ),
        )

    app.dependency_overrides[get_db] = override_db

    monkeypatch.setattr(
        "app.api.v1.routes.auth.login",
        fake_login,
    )

    try:
        response = client.post(
            "/auth/login",
            json={
                "email": "ADMIN@EXAMPLE.COM",
                "password": "secret",
            },
            headers={
                "x-forwarded-for": ("198.51.100.8, 10.0.0.1"),
                "user-agent": "pytest",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["access_token"] == "access"
    assert "refresh_token" not in response.json()
    assert response.cookies.get("scanx_refresh_token") == "refresh"
    assert response.cookies.get("scanx_csrf_token")
    assert isinstance(
        captured["db"],
        FakeDb,
    )
    assert captured["email"] == "ADMIN@example.com"
    assert captured["password"] == "secret"
    assert captured["ip_address"] == "198.51.100.8"
    assert captured["user_agent"] == "pytest"


def test_logout_route_revokes_refresh_token(
    client: TestClient,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_logout(
        db: FakeDb,
        *,
        refresh_token: str,
    ) -> None:
        captured["db"] = db
        captured["refresh_token"] = refresh_token

    app.dependency_overrides[get_db] = override_db

    monkeypatch.setattr(
        "app.api.v1.routes.auth.logout",
        fake_logout,
    )

    client.cookies.set("scanx_refresh_token", "refresh")
    client.cookies.set("scanx_csrf_token", "csrf-value")

    try:
        response = client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": "csrf-value"},
        )
    finally:
        app.dependency_overrides.clear()
        client.cookies.clear()

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully."}
    assert isinstance(
        captured["db"],
        FakeDb,
    )
    assert captured["refresh_token"] == "refresh"


def test_logout_route_rejects_missing_csrf_header(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_db] = override_db

    monkeypatch.setattr(
        "app.api.v1.routes.auth.logout",
        lambda db, *, refresh_token: None,
    )

    client.cookies.set("scanx_refresh_token", "refresh")
    client.cookies.set("scanx_csrf_token", "csrf-value")

    try:
        response = client.post("/auth/logout")
    finally:
        app.dependency_overrides.clear()
        client.cookies.clear()

    assert response.status_code == 403


def test_login_inactive_account_returns_generic_invalid_credentials(
    client: TestClient,
    db_session: Session,
) -> None:
    password = "InactiveAccount@123"

    create_test_user(
        db_session,
        email="inactive-login@example.com",
        password=password,
        role_code="admin",
        is_active=False,
    )

    db_session.commit()

    response = client.post(
        "/auth/login",
        json={
            "email": "inactive-login@example.com",
            "password": password,
        },
    )

    # Same status and message as a wrong password - a distinct response
    # here would let a caller learn the account exists but is deactivated.
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_login_unknown_email_returns_generic_invalid_credentials(
    client: TestClient,
) -> None:
    response = client.post(
        "/auth/login",
        json={
            "email": "definitely-not-a-user@example.com",
            "password": "WhateverPassword@123",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_me_route_returns_current_user(
    client: TestClient,
) -> None:
    user = CCUser(
        id=1,
        email="admin@example.com",
        first_name="Ada",
        last_name="Lovelace",
        phone=None,
        password_hash="hash",
        is_active=True,
        must_change_password=False,
        failed_login_attempts=0,
    )

    user.roles = []
    user.clinic_access = []

    app.dependency_overrides[get_current_user] = lambda: user

    try:
        response = client.get("/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"


def test_must_change_password_blocks_permission_gated_routes(
    client: TestClient,
    db_session: Session,
) -> None:
    password = "MustChange@123"

    create_test_user(
        db_session,
        email="mustchange@example.com",
        password=password,
        role_code="admin",
        must_change_password=True,
    )

    db_session.commit()

    login_response = client.post(
        "/auth/login",
        json={
            "email": "mustchange@example.com",
            "password": password,
        },
    )

    assert login_response.status_code == 200
    assert login_response.json()["user"]["must_change_password"] is True

    access_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    # A permission-gated business route must reject the request.
    blocked_response = client.get("/clinics", headers=headers)
    assert blocked_response.status_code == 403

    # /auth/me and /auth/change-password must remain reachable, since the
    # user needs both to discover and clear the must_change_password state.
    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200

    change_password_response = client.post(
        "/auth/change-password",
        headers=headers,
        json={
            "current_password": password,
            "new_password": "BrandNewPass@456",
            "confirm_new_password": "BrandNewPass@456",
        },
    )
    assert change_password_response.status_code == 200


def test_forgot_password_sends_email_and_omits_token(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    create_test_user(
        db_session,
        email="forgot@example.com",
        password="OriginalPass@123",
        role_code="admin",
    )

    db_session.commit()

    sent: dict[str, object] = {}

    def fake_send_email(**kwargs: object) -> str:
        sent.update(kwargs)
        return "fake-message-id"

    monkeypatch.setattr(
        "app.services.auth.send_email",
        fake_send_email,
    )

    response = client.post(
        "/auth/forgot-password",
        json={"email": "forgot@example.com"},
    )

    assert response.status_code == 200
    assert "reset_token" not in response.json()
    assert sent["to"] == ["forgot@example.com"]
    assert "reset-password?token=" in sent["html_body"]


def test_forgot_password_unknown_email_does_not_send_and_is_generic(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[object] = []

    monkeypatch.setattr(
        "app.services.auth.send_email",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.post(
        "/auth/forgot-password",
        json={"email": "no-such-user@example.com"},
    )

    assert response.status_code == 200
    assert "reset_token" not in response.json()
    assert calls == []


def test_login_refresh_logout_cookie_flow(
    client: TestClient,
    db_session: Session,
) -> None:
    password = "CookieFlow@123"

    create_test_user(
        db_session,
        email="cookieflow@example.com",
        password=password,
        role_code="admin",
    )

    db_session.commit()

    login_response = client.post(
        "/auth/login",
        json={
            "email": "cookieflow@example.com",
            "password": password,
        },
    )

    assert login_response.status_code == 200
    assert "refresh_token" not in login_response.json()
    assert login_response.json()["access_token"]

    refresh_cookie = login_response.cookies.get("scanx_refresh_token")
    csrf_cookie = login_response.cookies.get("scanx_csrf_token")

    assert refresh_cookie
    assert csrf_cookie

    # No CSRF header at all -> rejected.
    no_csrf_response = client.post("/auth/refresh")
    assert no_csrf_response.status_code == 403

    # CSRF header that doesn't match the cookie -> rejected.
    bad_csrf_response = client.post(
        "/auth/refresh",
        headers={"X-CSRF-Token": "not-the-real-token"},
    )
    assert bad_csrf_response.status_code == 403

    # Matching CSRF header -> succeeds and rotates the refresh token.
    refresh_response = client.post(
        "/auth/refresh",
        headers={"X-CSRF-Token": csrf_cookie},
    )

    assert refresh_response.status_code == 200
    assert "refresh_token" not in refresh_response.json()

    rotated_refresh_cookie = refresh_response.cookies.get("scanx_refresh_token")
    rotated_csrf_cookie = refresh_response.cookies.get("scanx_csrf_token")

    assert rotated_refresh_cookie
    assert rotated_refresh_cookie != refresh_cookie

    # Logout clears the session (and the cookies).
    logout_response = client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": rotated_csrf_cookie},
    )
    assert logout_response.status_code == 200

    # The pre-rotation refresh token was already revoked by the earlier
    # rotation, so replaying it must fail even with a valid CSRF pairing.
    client.cookies.set("scanx_refresh_token", refresh_cookie)
    client.cookies.set("scanx_csrf_token", rotated_csrf_cookie)

    stale_response = client.post(
        "/auth/refresh",
        headers={"X-CSRF-Token": rotated_csrf_cookie},
    )
    assert stale_response.status_code == 401
