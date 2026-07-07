from __future__ import annotations

import pytest

from app.scripts.bootstrap_admin import normalize_email, validate_password


def test_normalize_email_trims_and_lowercases() -> None:
    assert normalize_email(" Admin@Example.COM ") == "admin@example.com"


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("Short1!", "at least 12 characters"),
        ("lowercase-only-1!", "uppercase"),
        ("UPPERCASE-ONLY-1!", "lowercase"),
        ("NoNumbersHere!", "number"),
        ("NoSpecial123", "special"),
    ],
)
def test_validate_password_rejects_weak_passwords(password: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_password(password)


def test_validate_password_accepts_strong_password() -> None:
    validate_password("StrongPassword123!")
