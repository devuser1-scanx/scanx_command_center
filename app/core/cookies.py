from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings

REFRESH_TOKEN_COOKIE_NAME = "scanx_refresh_token"
CSRF_TOKEN_COOKIE_NAME = "scanx_csrf_token"

# Path=/ (not /auth): the CSRF cookie has to be readable by
# document.cookie on whatever page the browser is currently showing -
# e.g. /front-desk/dashboard - and Path scoping for JS-readability is
# based on the CURRENT PAGE's URL, not the URL of the API request that
# will eventually use it. Scoping this to /auth made document.cookie
# never return it outside auth-specific pages, so every refresh attempt
# from a normal app page failed CSRF validation with 403.
_COOKIE_PATH = "/"


def set_auth_cookies(
    response: Response,
    *,
    refresh_token: str,
    csrf_token: str,
) -> None:
    max_age = settings.jwt_refresh_token_expire_days * 24 * 60 * 60

    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="none",
        path=_COOKIE_PATH,
    )

    # Deliberately NOT HttpOnly: the frontend reads this and echoes it
    # back as a header (double-submit CSRF check) - see require_csrf_token.
    response.set_cookie(
        key=CSRF_TOKEN_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=True,
        samesite="none",
        path=_COOKIE_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        path=_COOKIE_PATH,
        secure=True,
        samesite="none",
    )
    response.delete_cookie(
        key=CSRF_TOKEN_COOKIE_NAME,
        path=_COOKIE_PATH,
        secure=True,
        samesite="none",
    )


def get_refresh_token_from_cookie(request: Request) -> str:
    token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing.",
        )

    return token


def require_csrf_token(request: Request) -> None:
    """Double-submit CSRF check for the cookie-authenticated auth routes.

    The refresh/CSRF cookies are SameSite=None (the frontend and backend
    run on different origins), so SameSite alone can't stop a forged
    cross-site request from carrying the refresh cookie automatically.
    The CSRF cookie is deliberately readable by frontend JS so it can be
    echoed back as a header - something a cross-site attacker can't do,
    since browsers never let one origin read another origin's cookies.
    """
    cookie_value = request.cookies.get(CSRF_TOKEN_COOKIE_NAME)
    header_value = request.headers.get("x-csrf-token")

    if not cookie_value or not header_value or not hmac.compare_digest(cookie_value, header_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid CSRF token.",
        )
