from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_utc,
    hash_password,
    hash_token,
    validate_password_strength,
    verify_password,
)


def test_password_hashes_verify_only_matching_passwords() -> None:
    password_digest = hash_password(
        "StrongPass123!"
    )

    assert verify_password(
        "StrongPass123!",
        password_digest,
    ) is True

    assert verify_password(
        "wrong-password",
        password_digest,
    ) is False


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "alllowercase123!",
        "ALLUPPERCASE123!",
        "NoNumbersHere!",
        "NoSpecialCharacter123",
    ],
)
def test_weak_passwords_are_rejected(
    password: str,
) -> None:
    with pytest.raises(
        ValueError
    ):
        validate_password_strength(
            password
        )


def test_access_token_round_trip_includes_claims() -> None:
    token = create_access_token(
        subject="42",
        additional_claims={
            "roles": ["admin"],
            "permissions": [
                "users.view"
            ],
        },
    )

    payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert payload["roles"] == [
        "admin"
    ]
    assert payload["permissions"] == [
        "users.view"
    ]
    assert payload["jti"]


def test_refresh_token_round_trip_includes_session_id() -> None:
    token = create_refresh_token(
        subject="42",
        session_id=7,
    )

    payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["type"] == "refresh"
    assert payload["session_id"] == 7


def test_expired_token_is_rejected() -> None:
    token = create_access_token(
        subject="42",
        expires_delta=timedelta(
            seconds=-1
        ),
    )

    with pytest.raises(
        ValueError,
        match="Invalid or expired token",
    ):
        decode_token(token)


def test_token_with_invalid_signature_is_rejected() -> None:
    now = datetime.now(UTC)

    token = jwt.encode(
        {
            "sub": "42",
            "type": "access",
            "iat": now,
            "exp": now + timedelta(
                minutes=5
            ),
        },
        "a-different-secret-key-that-is-long-enough",
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(
        ValueError,
        match="Invalid or expired token",
    ):
        decode_token(token)


def test_token_missing_subject_is_rejected() -> None:
    now = datetime.now(UTC)

    token = jwt.encode(
        {
            "type": "access",
            "iat": now,
            "exp": now + timedelta(
                minutes=5
            ),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(
        ValueError,
        match="Token subject is missing",
    ):
        decode_token(token)


def test_token_missing_type_is_rejected() -> None:
    now = datetime.now(UTC)

    token = jwt.encode(
        {
            "sub": "42",
            "iat": now,
            "exp": now + timedelta(
                minutes=5
            ),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(
        ValueError,
        match="Token type is missing",
    ):
        decode_token(token)


def test_hash_token_is_stable_sha256_hex_digest() -> None:
    digest = hash_token(
        "refresh-token"
    )

    assert digest == hash_token(
        "refresh-token"
    )
    assert len(digest) == 64


def test_ensure_utc_adds_timezone_to_naive_datetime() -> None:
    value = datetime(
        2026,
        1,
        1,
        12,
        0,
        0,
    )

    converted = ensure_utc(value)

    assert converted.tzinfo == UTC
    assert converted.hour == 12


def test_ensure_utc_preserves_aware_datetime() -> None:
    value = datetime(
        2026,
        1,
        1,
        12,
        0,
        0,
        tzinfo=UTC,
    )

    converted = ensure_utc(value)

    assert converted == value
    assert converted.tzinfo == UTC