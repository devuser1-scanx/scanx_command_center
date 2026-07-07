from __future__ import annotations

import pytest

from app.core.config import Settings


def test_debug_string_values_are_normalized() -> None:
    assert Settings(debug="local").debug is True
    assert Settings(debug="production").debug is False


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(cors_allowed_origins=" http://localhost:3000, ,https://scanx.test ")

    assert settings.cors_origins_list == [
        "http://localhost:3000",
        "https://scanx.test",
    ]


def test_database_uri_uses_database_url_when_provided() -> None:
    settings = Settings(database_url="postgresql+psycopg://example")

    assert settings.sqlalchemy_database_uri == "postgresql+psycopg://example"


def test_database_uri_requires_password_without_database_url() -> None:
    settings = Settings(database_url=None, database_password=None)

    with pytest.raises(ValueError, match="DATABASE_URL or DATABASE_PASSWORD"):
        _ = settings.sqlalchemy_database_uri


def test_database_uri_escapes_password() -> None:
    settings = Settings(
        database_url=None,
        database_host="/cloudsql/project:region:instance",
        database_name="scanx",
        database_user="scanx_user",
        database_password="pa ss/word",
    )

    assert settings.sqlalchemy_database_uri == (
        "postgresql+psycopg://scanx_user:pa+ss%2Fword@/scanx?host=/cloudsql/project:region:instance"
    )


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        (
            "jwt_access_token_expire_minutes",
            {"jwt_access_token_expire_minutes": 0},
        ),
        (
            "jwt_refresh_token_expire_days",
            {"jwt_refresh_token_expire_days": 0},
        ),
    ],
)
def test_jwt_expiry_values_must_be_positive(
    field_name: str,
    kwargs: dict[str, int],
) -> None:
    with pytest.raises(ValueError) as exc_info:
        Settings(**kwargs)

    assert field_name in str(exc_info.value)
