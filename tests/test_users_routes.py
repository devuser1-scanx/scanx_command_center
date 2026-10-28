from __future__ import annotations

import pytest
from conftest import create_test_user
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import CCUser, CCUserActivityAudit
from app.services.users import assign_roles, delete_user, set_user_status


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

    created_user = db_session.scalar(select(CCUser).where(CCUser.email == "new-hire@example.com"))
    assert created_user is not None
    assert created_user.must_change_password is True


def test_admin_reset_password_generates_and_emails_new_password(
    client: TestClient,
    admin_auth_headers: dict[str, str],
    sales_user: CCUser,
    monkeypatch,
) -> None:
    sent: dict[str, object] = {}

    monkeypatch.setattr(
        "app.services.users.send_email",
        lambda **kwargs: sent.update(kwargs) or "fake-message-id",
    )

    response = client.post(
        f"/users/{sales_user.id}/reset-password",
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert sent["to"] == [sales_user.email]
    assert "Temporary password:" in sent["html_body"]


def test_delete_user_removes_account_and_keeps_audit_entry(
    client: TestClient,
    admin_user: CCUser,
    admin_auth_headers: dict[str, str],
    sales_user: CCUser,
    db_session: Session,
) -> None:
    deleted_id = sales_user.id

    response = client.delete(f"/users/{deleted_id}", headers=admin_auth_headers)

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(CCUser, deleted_id) is None

    entry = db_session.scalar(
        select(CCUserActivityAudit).where(
            CCUserActivityAudit.action == "users.deleted",
            CCUserActivityAudit.resource_id == str(deleted_id),
        )
    )
    assert entry is not None
    assert entry.actor_user_id == admin_user.id
    assert entry.details["email"] == "sales@example.com"


def test_delete_user_unknown_id_is_404(
    client: TestClient,
    admin_auth_headers: dict[str, str],
) -> None:
    response = client.delete("/users/999999", headers=admin_auth_headers)

    assert response.status_code == 404


def test_delete_user_requires_the_delete_permission(
    client: TestClient,
    admin_user: CCUser,
    sonographer_auth_headers: dict[str, str],
) -> None:
    response = client.delete(f"/users/{admin_user.id}", headers=sonographer_auth_headers)

    assert response.status_code == 403


def test_admin_cannot_delete_or_deactivate_themselves(
    client: TestClient,
    admin_user: CCUser,
    admin_auth_headers: dict[str, str],
) -> None:
    deleted = client.delete(f"/users/{admin_user.id}", headers=admin_auth_headers)
    deactivated = client.put(
        f"/users/{admin_user.id}/status",
        headers=admin_auth_headers,
        json={"is_active": False},
    )

    assert deleted.status_code == 400
    assert deactivated.status_code == 400


def test_last_active_admin_cannot_be_deleted_deactivated_or_demoted(
    db_session: Session,
    admin_user: CCUser,
    sales_user: CCUser,
) -> None:
    # The actor is not an admin themselves, so only the last-admin rule applies.
    context = {"actor_user": sales_user, "ip_address": None, "user_agent": None}

    with pytest.raises(HTTPException) as deleting:
        delete_user(db_session, user_id=admin_user.id, **context)

    with pytest.raises(HTTPException) as deactivating:
        set_user_status(db_session, user_id=admin_user.id, is_active=False, **context)

    with pytest.raises(HTTPException) as demoting:
        assign_roles(db_session, user_id=admin_user.id, role_codes=["sales"], **context)

    for error in (deleting, deactivating, demoting):
        assert error.value.status_code == 400
        assert "last active admin" in error.value.detail


def test_an_admin_can_be_removed_while_another_active_admin_remains(
    db_session: Session,
    admin_user: CCUser,
    sales_user: CCUser,
    admin_password: str,
) -> None:
    second_admin = create_test_user(
        db_session,
        email="second-admin@example.com",
        password=admin_password,
        role_code="admin",
    )
    db_session.commit()

    delete_user(
        db_session,
        user_id=second_admin.id,
        actor_user=sales_user,
        ip_address=None,
        user_agent=None,
    )

    db_session.expire_all()
    assert db_session.get(CCUser, second_admin.id) is None
    assert db_session.get(CCUser, admin_user.id) is not None
