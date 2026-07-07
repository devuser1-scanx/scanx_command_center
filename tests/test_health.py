from fastapi.testclient import TestClient


def test_health_check(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_check_uses_database_dependency(client: TestClient) -> None:
    from app.api.v1.routes.health import get_db
    from app.main import app

    class FakeDb:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement) -> None:
            self.statements.append(str(statement))

    fake_db = FakeDb()

    def override_db() -> FakeDb:
        return fake_db

    app.dependency_overrides[get_db] = override_db

    try:
        response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert fake_db.statements == ["SELECT 1"]
