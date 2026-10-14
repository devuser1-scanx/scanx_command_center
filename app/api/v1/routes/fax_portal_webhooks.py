from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.services.fax_portal_ingest import process_payload, store_payload

# Public, unauthenticated-by-JWT endpoints called by WestFax Comprehend. They
# authenticate with a shared token (and optionally an HMAC signature) instead.
#
# Never log request bodies, headers or the token here: the body is PHI.
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fax-portal/webhooks")

_HMAC_PREFIXES = ("sha256=", "sha256:", "v1=")


def _given_tokens(request: Request) -> list[str]:
    """Every credential the request presents: Bearer, X-Api-Key and ?token=.

    All are checked, so an empty or wrong value in one place (for example a
    connector set to "Bearer token" with no token entered) cannot hide a valid
    token supplied in another.
    """
    candidates: list[str] = []

    authorization = request.headers.get("authorization", "")

    if authorization.lower().startswith("bearer "):
        candidates.append(authorization[7:].strip())

    candidates.append(request.headers.get("x-api-key", ""))
    candidates.append(request.query_params.get("token", ""))

    return [candidate for candidate in candidates if candidate]


def _token_is_valid(request: Request, expected: str) -> bool:
    return any(
        hmac.compare_digest(given.encode(), expected.encode()) for given in _given_tokens(request)
    )


def _signature_is_valid(secret: str, body: bytes, headers: Any) -> bool:
    """True if any request header carries HMAC-SHA256(secret, body), as hex or
    base64, optionally prefixed ("sha256=..."). The connector does not
    document which header holds it, so every header is checked; pin the
    header name here once a real signed delivery has been inspected.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = (digest.hex(), base64.b64encode(digest).decode())

    for value in headers.values():
        candidate = value.strip()

        for prefix in _HMAC_PREFIXES:
            if candidate.lower().startswith(prefix):
                candidate = candidate[len(prefix) :]

        for option in expected:
            if hmac.compare_digest(candidate.lower().encode(), option.lower().encode()):
                return True

    return False


def _ingest(db: Session, body: bytes) -> dict[str, Any]:
    payload = store_payload(db, body)
    payload_id = payload.id

    try:
        result = process_payload(db, payload_id)
    except Exception as exc:
        # The raw delivery is already saved (nothing lost) and can be
        # re-parsed; answer 200 so WestFax does not hammer us with retries.
        db.rollback()

        logger.error(
            "Comprehend payload %s could not be processed (%s).",
            payload_id,
            type(exc).__name__,
        )

        return {"ok": True, "payload_id": payload_id, "parsed": False}

    return {
        "ok": True,
        "payload_id": payload_id,
        "parsed": result.parse_error is None,
        "duplicate": not result.created,
        "referrals": len(result.referral_ids),
    }


@router.api_route("/comprehend", methods=["GET", "HEAD"], include_in_schema=False)
def comprehend_webhook_probe() -> dict[str, Any]:
    """Lets the connector's "Validate URL" check, or a browser, confirm the
    URL is reachable. Reveals nothing and needs no token."""
    return {"ok": True, "service": "fax-portal-comprehend-webhook"}


@router.post("/comprehend")
async def receive_comprehend_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    expected_token = settings.comprehend_webhook_token

    if not expected_token:
        # Fail closed: a public endpoint with no secret configured.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook is not configured.",
        )

    if not _token_is_valid(request, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook credentials.",
        )

    max_bytes = settings.comprehend_webhook_max_body_bytes
    declared = request.headers.get("content-length")

    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Request body is too large.",
        )

    body = await request.body()

    if len(body) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Request body is too large.",
        )

    hmac_secret = settings.comprehend_webhook_hmac_secret

    if hmac_secret and not _signature_is_valid(hmac_secret, body, request.headers):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    # Storing the raw delivery failing (e.g. the database is down) is the one
    # case that must surface as an error, so WestFax retries it.
    try:
        return await run_in_threadpool(_ingest, db, body)
    except Exception as exc:
        logger.error("Comprehend delivery could not be stored (%s).", type(exc).__name__)

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not store the delivery; please retry.",
        ) from exc
