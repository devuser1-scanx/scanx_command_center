from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_roles_returns_seeded_roles(
    client: TestClient,
    admin_auth_headers: dict[str, str],
) -> None:
    response = client.get("/roles", headers=admin_auth_headers)

    assert response.status_code == 200
    codes = {role["code"] for role in response.json()}
    assert {"admin", "front_desk", "sonographer", "sales"}.issubset(codes)


def test_list_roles_requires_authentication(
    client: TestClient,
) -> None:
    response = client.get("/roles")

    assert response.status_code == 401
