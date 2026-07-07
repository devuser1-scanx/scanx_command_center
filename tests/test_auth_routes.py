from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user
from app.api.v1.routes.auth import get_db
from app.main import app
from app.models.auth import CCUser
from app.schemas.auth import CurrentUserResponse, TokenResponse


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
    ) -> TokenResponse:
        captured.update(
            db=db,
            email=email,
            password=password,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return TokenResponse(
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
                is_email_verified=True,
                must_change_password=False,
                last_login_at=None,
                roles=[],
                permissions=[],
                clinic_access=[],
            ),
        )

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("app.api.v1.routes.auth.login", fake_login)

    try:
        response = client.post(
            "/auth/login",
            json={"email": "ADMIN@EXAMPLE.COM", "password": "secret"},
            headers={
                "x-forwarded-for": "198.51.100.8, 10.0.0.1",
                "user-agent": "pytest",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["access_token"] == "access"
    assert isinstance(captured["db"], FakeDb)
    assert captured["email"] == "ADMIN@example.com"
    assert captured["password"] == "secret"
    assert captured["ip_address"] == "198.51.100.8"
    assert captured["user_agent"] == "pytest"


def test_logout_route_revokes_refresh_token(client: TestClient, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_logout(db: FakeDb, *, refresh_token: str) -> None:
        captured["db"] = db
        captured["refresh_token"] = refresh_token

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("app.api.v1.routes.auth.logout", fake_logout)

    try:
        response = client.post("/auth/logout", json={"refresh_token": "refresh"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully."}
    assert isinstance(captured["db"], FakeDb)
    assert captured["refresh_token"] == "refresh"


def test_me_route_returns_current_user(client: TestClient, monkeypatch) -> None:
    user = CCUser(
        id=1,
        email="admin@example.com",
        first_name="Ada",
        last_name="Lovelace",
        phone=None,
        password_hash="hash",
        is_active=True,
        is_email_verified=True,
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
