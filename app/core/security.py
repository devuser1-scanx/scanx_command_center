from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError(
            "Password must contain at least 12 characters."
        )

    if len(password) > 200:
        raise ValueError(
            "Password must not exceed 200 characters."
        )

    if not any(character.isupper() for character in password):
        raise ValueError(
            "Password must contain at least one uppercase letter."
        )

    if not any(character.islower() for character in password):
        raise ValueError(
            "Password must contain at least one lowercase letter."
        )

    if not any(character.isdigit() for character in password):
        raise ValueError(
            "Password must contain at least one number."
        )

    if not any(
        not character.isalnum()
        for character in password
    ):
        raise ValueError(
            "Password must contain at least one special character."
        )


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return password_hash.hash(password)


def verify_password(
    password: str,
    password_digest: str,
) -> bool:
    try:
        return password_hash.verify(
            password,
            password_digest,
        )
    except Exception:
        return False


def create_access_token(
    *,
    subject: str,
    additional_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)

    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )
    )

    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid4()),
    }

    if additional_claims:
        payload.update(additional_claims)

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(
    *,
    subject: str,
    session_id: int,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)

    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(
            days=settings.jwt_refresh_token_expire_days
        )
    )

    payload = {
        "sub": subject,
        "type": "refresh",
        "session_id": session_id,
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid4()),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except InvalidTokenError as exc:
        raise ValueError(
            "Invalid or expired token."
        ) from exc

    if not payload.get("sub"):
        raise ValueError(
            "Token subject is missing."
        )

    if not payload.get("type"):
        raise ValueError(
            "Token type is missing."
        )

    return payload


def generate_secure_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return sha256(
        token.encode("utf-8")
    ).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)