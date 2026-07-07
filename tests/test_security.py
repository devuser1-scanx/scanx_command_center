from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hashes_verify_only_matching_passwords() -> None:
    password_digest = hash_password("StrongPass123!")

    assert verify_password("StrongPass123!", password_digest) is True
    assert verify_password("wrong-password", password_digest) is False


def test_access_token_round_trip_includes_claims() -> None:
    token = create_access_token(
        subject="42",
        additional_claims={"roles": ["admin"], "permissions": ["users.view"]},
    )

    payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert payload["roles"] == ["admin"]
    assert payload["permissions"] == ["users.view"]
    assert payload["jti"]


def test_refresh_token_round_trip_includes_session_id() -> None:
    token = create_refresh_token(subject="42", session_id=7)

    payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["type"] == "refresh"
    assert payload["session_id"] == 7


def test_expired_token_is_rejected() -> None:
    token = create_access_token(subject="42", expires_delta=timedelta(seconds=-1))

    with pytest.raises(ValueError, match="Invalid or expired token"):
        decode_token(token)


def test_hash_token_is_stable_sha256_hex_digest() -> None:
    digest = hash_token("refresh-token")

    assert digest == hash_token("refresh-token")
    assert len(digest) == 64
