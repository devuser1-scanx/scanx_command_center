from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fax_portal_helpers import PASSWORD, make_user
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token, hash_token
from app.repositories.auth import get_session_by_refresh_token_hash
from app.services.auth import IssuedTokens, issue_tokens, login, refresh_tokens


@pytest.fixture
def user(db_session: Session) -> Any:
    return make_user(db_session, "sales")


def sign_in(db: Session, user: Any) -> IssuedTokens:
    return login(db, email=user.email, password=PASSWORD, ip_address=None, user_agent=None)


def refresh(db: Session, token: str) -> IssuedTokens:
    return refresh_tokens(db, refresh_token=token, ip_address=None, user_agent=None)


def session_of(db: Session, token: str) -> Any:
    db.expire_all()

    return get_session_by_refresh_token_hash(db, hash_token(token))


# ---------------------------------------------------------------------
# The maximum session length
# ---------------------------------------------------------------------


def test_the_refresh_token_records_when_the_user_signed_in(db_session: Session, user: Any) -> None:
    before = int(datetime.now(UTC).timestamp())

    issued = sign_in(db_session, user)

    assert before <= decode_token(issued.refresh_token)["auth_at"] <= before + 5


def test_every_refresh_carries_the_original_login_time_forward(
    db_session: Session, user: Any
) -> None:
    issued = sign_in(db_session, user)
    signed_in = decode_token(issued.refresh_token)["auth_at"]

    again = refresh(db_session, issued.refresh_token)
    third = refresh(db_session, again.refresh_token)

    assert decode_token(again.refresh_token)["auth_at"] == signed_in
    assert decode_token(third.refresh_token)["auth_at"] == signed_in


def test_a_session_cannot_be_refreshed_past_its_maximum_length(
    db_session: Session, user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = sign_in(db_session, user)
    monkeypatch.setattr(settings, "session_max_hours", 0)

    with pytest.raises(HTTPException) as error:
        refresh(db_session, issued.refresh_token)

    assert error.value.status_code == 401
    # And the session is revoked, not just refused once.
    assert session_of(db_session, issued.refresh_token).revoked_at is not None


def test_tokens_issued_late_in_a_session_never_outlive_it(db_session: Session, user: Any) -> None:
    started = datetime.now(UTC) - timedelta(hours=settings.session_max_hours) + timedelta(minutes=5)

    issued = issue_tokens(db_session, user=user, ip_address=None, user_agent=None, auth_at=started)

    deadline = started + timedelta(hours=settings.session_max_hours)
    refresh_exp = datetime.fromtimestamp(decode_token(issued.refresh_token)["exp"], UTC)
    access_exp = datetime.fromtimestamp(decode_token(issued.access_token)["exp"], UTC)
    # Five minutes left: neither token is good for the usual 15 minutes / 7 days.
    assert refresh_exp <= deadline + timedelta(seconds=2)
    assert access_exp <= deadline + timedelta(seconds=2)
    assert issued.expires_in <= 5 * 60 + 2
    assert session_of(db_session, issued.refresh_token).expires_at <= deadline + timedelta(
        seconds=2
    )


def test_a_normal_login_keeps_the_usual_token_lifetimes(db_session: Session, user: Any) -> None:
    issued = sign_in(db_session, user)

    assert issued.expires_in == settings.jwt_access_token_expire_minutes * 60
    exp = datetime.fromtimestamp(decode_token(issued.refresh_token)["exp"], UTC)
    # Capped by the maximum session length, which is shorter than the 7 days.
    assert exp - datetime.now(UTC) <= timedelta(hours=settings.session_max_hours, seconds=5)


def test_a_refresh_token_from_before_the_claim_existed_still_works(
    db_session: Session, user: Any
) -> None:
    import jwt

    from app.core.security import create_refresh_token
    from app.repositories.auth import create_session

    # A token issued before this change: the same token without the auth_at claim.
    payload = decode_token(create_refresh_token(subject=str(user.id)))
    without = {k: v for k, v in payload.items() if k != "auth_at"}
    token = jwt.encode(without, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    create_session(
        db_session,
        user_id=user.id,
        refresh_token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        user_agent=None,
        ip_address=None,
    )
    db_session.commit()

    assert refresh(db_session, token).access_token


# ---------------------------------------------------------------------
# A browser that went away
# ---------------------------------------------------------------------


def idle_limit() -> timedelta:
    return timedelta(
        minutes=settings.session_idle_timeout_minutes + settings.jwt_access_token_expire_minutes
    )


def test_a_session_refreshed_recently_can_be_refreshed_again(
    db_session: Session, user: Any
) -> None:
    issued = sign_in(db_session, user)
    session = session_of(db_session, issued.refresh_token)
    session.created_at = datetime.now(UTC) - idle_limit() + timedelta(minutes=2)
    db_session.commit()

    assert refresh(db_session, issued.refresh_token).access_token


def test_a_session_nobody_refreshed_for_too_long_must_sign_in_again(
    db_session: Session, user: Any
) -> None:
    issued = sign_in(db_session, user)
    session = session_of(db_session, issued.refresh_token)
    # No browser refreshed it for longer than the idle timeout plus a token's life:
    # the tab was closed or the computer was shut.
    session.created_at = datetime.now(UTC) - idle_limit() - timedelta(minutes=1)
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        refresh(db_session, issued.refresh_token)

    assert error.value.status_code == 401
    assert session_of(db_session, issued.refresh_token).revoked_at is not None


# ---------------------------------------------------------------------
# The policy the browser applies
# ---------------------------------------------------------------------


def test_the_session_policy_is_served_to_a_signed_in_browser(
    client: TestClient, db_session: Session, user: Any
) -> None:
    issued = sign_in(db_session, user)

    response = client.get(
        "/auth/session-policy", headers={"Authorization": f"Bearer {issued.access_token}"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "idle_timeout_seconds": settings.session_idle_timeout_minutes * 60,
        "warning_seconds": min(
            settings.session_warning_seconds, settings.session_idle_timeout_minutes * 60
        ),
        "max_session_seconds": settings.session_max_hours * 3600,
    }


def test_the_warning_is_never_longer_than_the_timeout(
    client: TestClient, db_session: Session, user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 1)
    monkeypatch.setattr(settings, "session_warning_seconds", 300)
    issued = sign_in(db_session, user)

    body = client.get(
        "/auth/session-policy", headers={"Authorization": f"Bearer {issued.access_token}"}
    ).json()

    assert (body["idle_timeout_seconds"], body["warning_seconds"]) == (60, 60)


def test_the_session_policy_needs_a_signed_in_user(client: TestClient) -> None:
    assert client.get("/auth/session-policy").status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_idle_timeout_minutes", 0),
        ("session_idle_timeout_minutes", 121),
        ("session_warning_seconds", 5),
        ("session_warning_seconds", 601),
        ("session_max_hours", 0),
        ("session_max_hours", 25),
    ],
)
def test_nonsense_timeout_settings_are_refused_at_startup(field: str, value: int) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(jwt_secret_key="x" * 40, **{field: value})
