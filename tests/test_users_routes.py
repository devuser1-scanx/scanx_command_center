from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import CCUser


def test_create_user_generates_password_and_emails_it(
    client: TestClient,
    admin_auth_headers: dict[str, str],
    db_session: Session,
    monkeypatch,
) -> None:
    sent: dict[str, object] = {}

    def fake_send_email(**kwargs: object) -> str:
        sent.update(kwargs)
        return "fake-message-id"

    monkeypatch.setattr(
        "app.services.users.send_email",
        fake_send_email,
    )

    response = client.post(
        "/users",
        headers=admin_auth_headers,
        # No temporary_password field - the caller can no longer choose
        # or even see the account's initial password.
        json={
            "email": "new-hire@example.com",
            "first_name": "New",
            "last_name": "Hire",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert "temporary_password" not in body
    assert "password" not in body
    assert body["must_change_password"] is True

    assert sent["to"] == ["new-hire@example.com"]
    assert "Temporary password:" in sent["html_body"]

    created_user = db_session.scalar(
        select(CCUser).where(CCUser.email == "new-hire@example.com")
    )
    assert created_user is not None
    assert created_user.must_change_password is True


def test_admin_reset_password_generates_and_emails_new_password(
    client: TestClient,
    admin_auth_headers: dict[str, str],
    front_desk_user: CCUser,
    monkeypatch,
) -> None:
    sent: dict[str, object] = {}

    monkeypatch.setattr(
        "app.services.users.send_email",
        lambda **kwargs: sent.update(kwargs) or "fake-message-id",
    )

    response = client.post(
        f"/users/{front_desk_user.id}/reset-password",
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert sent["to"] == [front_desk_user.email]
    assert "Temporary password:" in sent["html_body"]
