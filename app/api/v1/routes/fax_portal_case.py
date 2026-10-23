from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies.fax_portal import request_ip, require_fax_portal_access
from app.db.prod_session import get_prod_db
from app.db.session import get_db
from app.models.auth import CCUser
from app.models.fax_portal import CCFpReferralBoard
from app.repositories.auth import collect_role_codes
from app.schemas.fax_portal import (
    AvailableTime,
    BookingInfoResponse,
    CallResultRequest,
    CallResultResponse,
    CaseDetailResponse,
    DialCallResponse,
    DirectBookRequest,
    DirectBookResponse,
    DoNotCallResponse,
    FaxBackDraftResponse,
    FaxBackRequest,
    FaxBackResponse,
    IntakeUpdateRequest,
    IntakeUpdateResponse,
    MessageTemplateResponse,
    ReferralSearchItem,
    SendMessageRequest,
    SendMessageResponse,
    StartCallResponse,
)
from app.services import (
    fax_portal_booking,
    fax_portal_calls,
    fax_portal_case,
    fax_portal_direct_booking,
    fax_portal_fax_back,
    fax_portal_intake,
    fax_portal_messages,
)
from app.services.fax_portal_referrals import ReferralError

router = APIRouter(prefix="/fax-portal")


def _raise(db: Session, error: ReferralError) -> None:
    db.rollback()

    raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("/referrals/search", response_model=list[ReferralSearchItem])
def search_referrals_route(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[ReferralSearchItem]:
    return fax_portal_case.search_referrals(db, q, limit)


@router.get("/referrals/{referral_id}/case", response_model=CaseDetailResponse)
def get_case_route(
    referral_id: int,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> CaseDetailResponse:
    try:
        return fax_portal_case.get_case(db, referral_id)
    except ReferralError as error:
        _raise(db, error)
        raise


@router.patch("/referrals/{referral_id}/case", response_model=IntakeUpdateResponse)
def update_case_route(
    referral_id: int,
    payload: IntakeUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> IntakeUpdateResponse:
    """Edits the booking-form fields of an open referral (optimistic lock on
    row_version; before/after values audited)."""
    try:
        card, changed = fax_portal_intake.update_intake(
            db,
            referral_id,
            payload,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
            require_review=False,
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return IntakeUpdateResponse(id=card.id, row_version=card.row_version, changed_fields=changed)


# ---------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------


@router.post("/referrals/{referral_id}/calls", response_model=StartCallResponse)
def start_call_route(
    referral_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> StartCallResponse:
    """Starts a call row for the referral (a call placed outside the portal's
    click-to-call service is logged this way). Refused when the call limit is
    reached, the number is do-not-call, the card is closed, or a call is
    already waiting for its result."""
    try:
        call = fax_portal_case.start_call_log(
            db,
            referral_id,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return StartCallResponse(call_id=call.id, started_at=call.started_at)


@router.post("/referrals/{referral_id}/calls/dial", response_model=DialCallResponse)
def dial_patient_route(
    referral_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> DialCallResponse:
    """Click-to-call from the browser: logs the call and returns a short-lived
    token. The browser connects to Twilio with it, and Twilio dials the patient
    from the organisation's number. Same refusals as logging a call by hand."""
    try:
        return fax_portal_calls.dial_patient(
            db,
            referral_id,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise


@router.post("/calls/{call_id}/abandon", status_code=status.HTTP_204_NO_CONTENT)
def abandon_call_route(
    call_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> None:
    """The browser could not start the call (microphone refused, no connection):
    removes the call row so it does not count or block the next call."""
    try:
        fax_portal_calls.abandon_call(
            db,
            call_id,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise


@router.post("/calls/{call_id}/result", response_model=CallResultResponse)
def record_call_result_route(
    call_id: int,
    payload: CallResultRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> CallResultResponse:
    try:
        response = fax_portal_case.record_call_result(
            db,
            call_id,
            payload,
            user=current_user,
            role_codes=set(collect_role_codes(current_user)),
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return response


@router.post("/referrals/{referral_id}/do-not-call", response_model=DoNotCallResponse)
def do_not_call_route(
    referral_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> DoNotCallResponse:
    try:
        response = fax_portal_case.mark_do_not_call(
            db,
            referral_id,
            user=current_user,
            role_codes=set(collect_role_codes(current_user)),
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return response


# ---------------------------------------------------------------------
# Texts
# ---------------------------------------------------------------------


@router.get(
    "/referrals/{referral_id}/message-templates", response_model=list[MessageTemplateResponse]
)
def message_templates_route(
    referral_id: int,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[MessageTemplateResponse]:
    try:
        return fax_portal_messages.render_templates(db, referral_id)
    except ReferralError as error:
        _raise(db, error)
        raise


@router.post(
    "/referrals/{referral_id}/messages",
    response_model=SendMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def send_message_route(
    referral_id: int,
    payload: SendMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> SendMessageResponse:
    try:
        return fax_portal_messages.send_message(
            db,
            referral_id,
            payload.body,
            payload.template_code,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise


# ---------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------


@router.get("/referrals/{referral_id}/direct-booking/dates", response_model=list[str])
def direct_booking_dates_route(
    referral_id: int,
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[str]:
    """Days in `month` (YYYY-MM) with open times for the referral's exam and
    clinic, straight from Acuity."""
    try:
        return fax_portal_direct_booking.availability_dates(db, referral_id, month)
    except ReferralError as error:
        _raise(db, error)
        raise


@router.get("/referrals/{referral_id}/direct-booking/times", response_model=list[AvailableTime])
def direct_booking_times_route(
    referral_id: int,
    date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[AvailableTime]:
    try:
        return fax_portal_direct_booking.availability_times(db, referral_id, date)
    except ReferralError as error:
        _raise(db, error)
        raise


@router.post("/referrals/{referral_id}/direct-booking", response_model=DirectBookResponse)
def direct_book_route(
    referral_id: int,
    payload: DirectBookRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> DirectBookResponse:
    """Books the appointment in Acuity itself (no payment step: collected at
    check-in) and marks the referral Appointment Booked."""
    try:
        response = fax_portal_direct_booking.book(
            db,
            referral_id,
            time=payload.time,
            email=payload.email,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return response


@router.get("/referrals/{referral_id}/fax-back", response_model=FaxBackDraftResponse)
def fax_back_draft_route(
    referral_id: int,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> FaxBackDraftResponse:
    """What the "fax back to the referring physician" pop-up starts with: the
    number the referral came from, the subject, and the message with the list of
    contact attempts."""
    try:
        return fax_portal_fax_back.build_draft(db, referral_id)
    except ReferralError as error:
        _raise(db, error)
        raise


@router.post("/referrals/{referral_id}/fax-back", response_model=FaxBackResponse)
def fax_back_route(
    referral_id: int,
    payload: FaxBackRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> FaxBackResponse:
    """Faxes the referring physician (through WestFax's email-to-fax) that the
    patient could not be reached. The person can change the number, subject and
    message first; optionally moves the referral to Didn't Respond."""
    try:
        response = fax_portal_fax_back.send_fax_back(
            db,
            referral_id,
            payload,
            user=current_user,
            role_codes=set(collect_role_codes(current_user)),
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return response


@router.get("/referrals/{referral_id}/booking", response_model=BookingInfoResponse)
def booking_info_route(
    referral_id: int,
    db: Session = Depends(get_db),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> BookingInfoResponse:
    """The prefilled Acuity booking link and, from the production
    `appointment` table (read only), appointments that look like this
    patient's, so the booking can be linked to the referral."""
    card = db.get(CCFpReferralBoard, referral_id)

    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referral not found.")

    # The agent books on the patient's behalf, so the intake questions are
    # filled in too (the link texted to the patient is not).
    link, blocked = fax_portal_booking.build_booking_link(db, card, include_intake_fields=True)

    try:
        matches = fax_portal_booking.find_appointment_matches(prod_db, db, card)
        unavailable = None
    except Exception:  # noqa: BLE001 - a lookup failure must not break the case page
        matches, unavailable = [], "The appointment lookup is not available right now."

    return BookingInfoResponse(
        booking_link=link,
        booking_link_blocked_reason=blocked,
        matches=matches,
        matches_unavailable=unavailable,
    )
