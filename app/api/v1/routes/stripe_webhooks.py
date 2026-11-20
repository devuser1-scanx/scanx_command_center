from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.integrations.stripe_client import StripeSignatureError, verify_webhook_event
from app.services.image_invoices import apply_stripe_event

# Public endpoint called by Stripe. It carries no JWT; the Stripe-Signature
# header (HMAC-SHA256 under the endpoint's signing secret) is the credential,
# and it fails closed: with no STRIPE_WEBHOOK_SECRET_IMAGE_INVOICE nothing verifies.
#
# Never log the body or headers: an invoice event carries the patient's email.
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")

# Stripe invoice events are a few KB; anything bigger is not one.
MAX_BODY_BYTES = 1024 * 1024


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    payload = await request.body()

    if len(payload) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large.",
        )

    try:
        event = verify_webhook_event(payload, request.headers.get("stripe-signature"))
    except StripeSignatureError as exc:
        logger.warning("Rejected a Stripe webhook: %s", exc)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature.",
        ) from exc

    # An unexpected failure here propagates as a 500 so Stripe retries.
    await run_in_threadpool(apply_stripe_event, db, event)

    return {"status": "ok"}
