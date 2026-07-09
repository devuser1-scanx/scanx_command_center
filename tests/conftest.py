from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

load_dotenv()

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:admin@localhost:5432/test_db",
    ),
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-key-that-is-long-enough-for-validation",
)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault(
    "AUTH_MAX_FAILED_LOGIN_ATTEMPTS",
    "5",
)
os.environ.setdefault(
    "AUTH_ACCOUNT_LOCK_MINUTES",
    "15",
)
os.environ.setdefault(
    "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES",
    "30",
)

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.auth import (  # noqa: E402
    CCPermission,
    CCRole,
    CCRolePermission,
    CCUser,
    CCUserRole,
)

test_engine = create_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
)

PERMISSIONS = [
    ("users.view", "users", "View Users"),
    ("users.create", "users", "Create Users"),
    ("users.update", "users", "Update Users"),
    ("users.activate", "users", "Activate Users"),
    ("users.deactivate", "users", "Deactivate Users"),
    ("users.assign_role", "users", "Assign User Roles"),
    ("users.assign_clinic", "users", "Assign User Clinics"),
    ("roles.view", "roles", "View Roles"),
    ("roles.manage", "roles", "Manage Roles"),
    ("clinics.view", "clinics", "View Clinics"),
    ("clinics.assign", "clinics", "Assign Clinics"),
    ("patients.search", "patients", "Search Patients"),
    ("patients.view", "patients", "View Patients"),
    ("dashboard.view", "dashboard", "View Dashboard"),
    ("appointments.view", "appointments", "View Appointments"),
    ("appointments.update", "appointments", "Update Appointments"),
    ("messages.view", "messages", "View Messages"),
    ("messages.send", "messages", "Send Messages"),
    ("calls.view", "calls", "View Calls"),
    ("calls.create", "calls", "Create Calls"),
    ("reports.view", "reports", "View Reports"),
    ("reports.manage", "reports", "Manage Reports"),
    ("cases.view", "cases", "View Cases"),
    ("cases.manage", "cases", "Manage Cases"),
    ("tasks.view", "tasks", "View Tasks"),
    ("tasks.manage", "tasks", "Manage Tasks"),
    ("settings.view", "settings", "View Settings"),
    ("settings.manage", "settings", "Manage Settings"),
    ("audit.view", "audit", "View Audit Logs"),
]


def ensure_role(
    db: Session,
    *,
    code: str,
    name: str,
) -> CCRole:
    role = db.scalar(
        select(CCRole).where(
            CCRole.code == code
        )
    )

    if role is None:
        role = CCRole(
            code=code,
            name=name,
            description=f"{name} test role.",
            is_system_role=True,
            is_active=True,
        )
        db.add(role)
        db.flush()

    return role


def ensure_permission(
    db: Session,
    *,
    code: str,
    module: str,
    name: str,
) -> CCPermission:
    permission = db.scalar(
        select(CCPermission).where(
            CCPermission.code == code
        )
    )

    if permission is None:
        permission = CCPermission(
            code=code,
            module=module,
            name=name,
            description=f"{name} test permission.",
            is_active=True,
        )
        db.add(permission)
        db.flush()

    return permission


def ensure_role_permission(
    db: Session,
    *,
    role: CCRole,
    permission: CCPermission,
) -> None:
    mapping = db.scalar(
        select(CCRolePermission).where(
            CCRolePermission.role_id == role.id,
            CCRolePermission.permission_id == permission.id,
        )
    )

    if mapping is None:
        db.add(
            CCRolePermission(
                role_id=role.id,
                permission_id=permission.id,
            )
        )


def seed_auth_catalog(
    db: Session,
) -> None:
    admin_role = ensure_role(
        db,
        code="admin",
        name="Admin",
    )

    ensure_role(
        db,
        code="front_desk",
        name="Front Desk",
    )

    sonographer_role = ensure_role(
        db,
        code="sonographer",
        name="Sonographer",
    )

    ensure_role(
        db,
        code="sales",
        name="Sales",
    )

    permission_map: dict[
        str,
        CCPermission,
    ] = {}

    for code, module, name in PERMISSIONS:
        permission = ensure_permission(
            db,
            code=code,
            module=module,
            name=name,
        )

        permission_map[code] = permission

        ensure_role_permission(
            db,
            role=admin_role,
            permission=permission,
        )

    for code in (
        "patients.search",
        "patients.view",
    ):
        ensure_role_permission(
            db,
            role=sonographer_role,
            permission=permission_map[code],
        )

    db.flush()


def create_test_user(
    db: Session,
    *,
    email: str,
    password: str,
    role_code: str,
    first_name: str = "Test",
    last_name: str = "User",
    is_active: bool = True,
    must_change_password: bool = False,
) -> CCUser:
    role = db.scalar(
        select(CCRole).where(
            CCRole.code == role_code
        )
    )

    if role is None:
        raise RuntimeError(
            f"Required test role "
            f"'{role_code}' does not exist."
        )

    user = CCUser(
        email=email.strip().lower(),
        first_name=first_name,
        last_name=last_name,
        phone=None,
        password_hash=hash_password(password),
        is_active=is_active,
        is_email_verified=True,
        must_change_password=must_change_password,
        failed_login_attempts=0,
    )

    db.add(user)
    db.flush()

    db.add(
        CCUserRole(
            user_id=user.id,
            role_id=role.id,
        )
    )

    db.flush()

    return user


@pytest.fixture(
    scope="session",
    autouse=True,
)
def verify_test_database() -> Generator[
    None,
    None,
    None,
]:
    database_url = TEST_DATABASE_URL.lower()

    if "test" not in database_url:
        raise RuntimeError(
            "Tests must run against a database "
            "whose URL contains 'test'. "
            "Set TEST_DATABASE_URL to a "
            "dedicated test database."
        )

    yield


@pytest.fixture
def db_connection() -> Generator[
    Connection,
    None,
    None,
]:
    connection = test_engine.connect()
    transaction = connection.begin()

    try:
        yield connection
    finally:
        if transaction.is_active:
            transaction.rollback()

        connection.close()


@pytest.fixture
def db_session(
    db_connection: Connection,
) -> Generator[
    Session,
    None,
    None,
]:
    session = Session(
        bind=db_connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    seed_auth_catalog(session)
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(
    db_session: Session,
) -> Generator[
    TestClient,
    None,
    None,
]:
    def override_get_db() -> Generator[
        Session,
        None,
        None,
    ]:
        yield db_session

    app.dependency_overrides[
        get_db
    ] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def admin_password() -> str:
    return "AdminPassword@123"


@pytest.fixture
def front_desk_password() -> str:
    return "FrontDeskPass@123"


@pytest.fixture
def sonographer_password() -> str:
    return "Sonographer@123"


@pytest.fixture
def admin_user(
    db_session: Session,
    admin_password: str,
) -> CCUser:
    user = create_test_user(
        db_session,
        email="admin@example.com",
        password=admin_password,
        role_code="admin",
        first_name="Admin",
        last_name="User",
    )

    db_session.commit()

    return user


@pytest.fixture
def sonographer_user(
    db_session: Session,
    sonographer_password: str,
) -> CCUser:
    user = create_test_user(
        db_session,
        email="sonographer@example.com",
        password=sonographer_password,
        role_code="sonographer",
        first_name="Sono",
        last_name="Grapher",
    )

    db_session.commit()

    return user


@pytest.fixture
def front_desk_user(
    db_session: Session,
    front_desk_password: str,
) -> CCUser:
    user = create_test_user(
        db_session,
        email="frontdesk@example.com",
        password=front_desk_password,
        role_code="front_desk",
        first_name="Front",
        last_name="Desk",
    )

    db_session.commit()

    return user


@pytest.fixture
def admin_tokens(
    client: TestClient,
    admin_user: CCUser,
    admin_password: str,
) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={
            "email": admin_user.email,
            "password": admin_password,
        },
    )

    assert response.status_code == 200

    payload = response.json()

    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
    }


@pytest.fixture
def admin_auth_headers(
    admin_tokens: dict[str, str],
) -> dict[str, str]:
    return {
        "Authorization": (
            "Bearer "
            f"{admin_tokens['access_token']}"
        )
    }


@pytest.fixture
def sonographer_tokens(
    client: TestClient,
    sonographer_user: CCUser,
    sonographer_password: str,
) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={
            "email": sonographer_user.email,
            "password": sonographer_password,
        },
    )

    assert response.status_code == 200

    payload = response.json()

    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
    }


@pytest.fixture
def sonographer_auth_headers(
    sonographer_tokens: dict[str, str],
) -> dict[str, str]:
    return {
        "Authorization": (
            "Bearer "
            f"{sonographer_tokens['access_token']}"
        )
    }