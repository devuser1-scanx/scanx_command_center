from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.integrations.twilio_client import validate_twilio_signature
from app.services import fax_portal_calls
from app.services.fax_portal_messages import STATUS_CALLBACK_PATH, apply_status_callback

# Called by Twilio, not by a signed-in user: authenticated by Twilio's
# X-Twilio-Signature instead. Never log the form body (it holds phone numbers).
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fax-portal/webhooks/twilio")

TWIML = "text/xml"


async def _verified_params(request: Request, path: str) -> dict[str, str]:
    """The form Twilio posted, after checking its signature. The signed URL is
    built from the configured public base URL when there is one (behind Cloud
    Run the request URL's scheme/host can differ from what Twilio was given),
    otherwise from the request itself."""
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    if not validate_twilio_signature(
        url=fax_portal_calls.signed_url(path) or str(request.url),
        params=params,
        signature=request.headers.get("x-twilio-signature"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Twilio signature.",
        )

    return params


@router.post("/message-status", status_code=status.HTTP_204_NO_CONTENT)
async def message_status_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    params = await _verified_params(request, STATUS_CALLBACK_PATH)

    sid = params.get("MessageSid") or params.get("SmsSid")
    message_status = params.get("MessageStatus") or params.get("SmsStatus")

    if sid and message_status:
        apply_status_callback(
            db,
            message_sid=sid,
            status=message_status,
            error_code=params.get("ErrorCode") or None,
            from_number=params.get("From"),
        )
        db.commit()

    # Always acknowledge a correctly signed request so Twilio does not retry.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Click-to-call
# ---------------------------------------------------------------------


@router.post("/voice/client")
async def voice_client_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """The TwiML App's Voice URL: Twilio asks this when a staff member's browser
    starts a call (`CallId` is the call row the browser was given). Answers with
    the instruction to dial the patient from the organisation's number."""
    params = await _verified_params(request, fax_portal_calls.CLIENT_PATH)
    twiml = fax_portal_calls.client_twiml(db, params)
    db.commit()

    return Response(content=twiml, media_type=TWIML)


@router.post("/voice/dial-done")
async def voice_dial_done_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Twilio posts here when the patient's leg ends (answered, busy, no
    answer...). Recorded on the call so the agent gets a suggested result."""
    params = await _verified_params(request, fax_portal_calls.DIAL_DONE_PATH)

    fax_portal_calls.apply_dial_result(
        db,
        call_sid=params.get("CallSid", ""),
        dial_status=params.get("DialCallStatus"),
        dial_duration=params.get("DialCallDuration"),
    )
    db.commit()

    return Response(content=fax_portal_calls.no_content_twiml(), media_type=TWIML)


@router.post("/voice/status", status_code=status.HTTP_204_NO_CONTENT)
async def voice_status_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    params = await _verified_params(request, fax_portal_calls.STATUS_PATH)

    fax_portal_calls.apply_call_status(
        db,
        call_sid=params.get("CallSid", ""),
        call_status=params.get("CallStatus"),
    )
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
