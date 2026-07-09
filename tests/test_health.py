from fastapi.testclient import TestClient


def test_health_check(
    client: TestClient,
) -> None:
    response = client.get(
        "/health"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_check_uses_database_dependency(
    client: TestClient,
) -> None:
    response = client.get(
        "/ready"
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready"
    }