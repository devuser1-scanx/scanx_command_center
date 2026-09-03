from __future__ import annotations

import logging
import secrets
import string
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pinned explicitly rather than PasswordHash.recommended(), so the actual
# hashing cost can't silently change out from under existing hashes on a
# pwdlib upgrade. These values match pwdlib/argon2-cffi's own current
# defaults (Argon2id, 64 MiB memory, 3 iterations, 4-way parallelism) -
# RFC 9106's second recommended option for environments without a strict
# memory ceiling.
password_hash = PasswordHash(
    [
        Argon2Hasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
        )
    ]
)

# JWT audience/issuer claims - not currently verified against anything
# external (this is a single-service system), but set so a token can't be
# silently accepted by some future second verifier that doesn't check them.
JWT_ISSUER = "scanx-command-center-api"
JWT_AUDIENCE = "scanx-command-center"


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")

    if len(password) > 200:
        raise ValueError("Password must not exceed 200 characters.")

    if not any(character.isupper() for character in password):
        raise ValueError("Password must contain at least one uppercase letter.")

    if not any(character.islower() for character in password):
        raise ValueError("Password must contain at least one lowercase letter.")

    if not any(character.isdigit() for character in password):
        raise ValueError("Password must contain at least one number.")

    if not any(not character.isalnum() for character in password):
        raise ValueError("Password must contain at least one special character.")


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
        # A malformed/unrecognized digest string raises here rather than
        # returning a clean mismatch - treated as "not verified" either
        # way, but still worth logging since it would otherwise be a
        # silent failure mode.
        logger.exception("Password verification raised unexpectedly.")
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
        else timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )

    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid4()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
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
    expires_delta: timedelta | None = None,
) -> str:
    """Session identity is never carried in this token - validity is
    determined entirely by looking up hash_token(token) against
    CCSession.refresh_token_hash (see repositories/auth.py). Encoding a
    session id here would be redundant data no code ever reads back.
    """
    now = datetime.now(UTC)

    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(days=settings.jwt_refresh_token_expire_days)
    )

    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid4()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
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
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired token.") from exc

    if not payload.get("sub"):
        raise ValueError("Token subject is missing.")

    if not payload.get("type"):
        raise ValueError("Token type is missing.")

    return payload


def generate_secure_token() -> str:
    return secrets.token_urlsafe(48)


_TEMP_PASSWORD_SPECIAL_CHARS = "!@#$%^&*()-_=+"


def generate_temporary_password(length: int = 16) -> str:
    """Generates a random password for admin-created accounts / admin
    password resets, guaranteed to satisfy validate_password_strength()
    (at least one upper/lower/digit/special character).

    Replaces having the admin type/choose one themselves - it now only
    ever exists as this generated value, delivered to the user by email.
    """
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_TEMP_PASSWORD_SPECIAL_CHARS),
    ]

    all_chars = (
        string.ascii_uppercase
        + string.ascii_lowercase
        + string.digits
        + _TEMP_PASSWORD_SPECIAL_CHARS
    )
    remaining_count = max(length - len(required), 0)
    remaining = [secrets.choice(all_chars) for _ in range(remaining_count)]

    password_chars = required + remaining
    secrets.SystemRandom().shuffle(password_chars)

    return "".join(password_chars)


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
