from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies.fax_portal import request_ip
from app.db.session import get_db
from app.services import fax_portal_prefill_link
from app.services.fax_portal_prefill_link import LinkError

# Opened by the patient from the text we sent, not by a signed-in user: the
# signed token in the address is the credential. Nothing is cached, and the
# page that follows is told not to send a Referer.
router = APIRouter(prefix="/fax-portal/book")

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _page(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>ScanX Health</title></head>"
        "<body style='font-family:Arial,sans-serif;max-width:32rem;margin:15vh auto;"
        "padding:0 1rem;text-align:center;color:#2d2d2d'>"
        f"<h2>ScanX Health</h2><p>{html.escape(message)}</p></body></html>",
        status_code=status_code,
        headers=_NO_STORE,
    )


@router.get("/{token}", include_in_schema=False, response_model=None)
def open_booking_link(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    try:
        url = fax_portal_prefill_link.open_link(
            db,
            token,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except LinkError as error:
        db.rollback()

        return _page(error.message, 410)

    return RedirectResponse(url, status_code=302, headers=_NO_STORE)
