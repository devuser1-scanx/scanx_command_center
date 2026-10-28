from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.api.dependencies.fax_portal import request_ip
from app.db.prod_session import get_prod_db
from app.db.session import get_db
from app.models.auth import CCUser
from app.repositories import calls as calls_repository
from app.repositories import production_writes
from app.schemas.calls import (
    CallPatientHistoryResponse,
    CallPatientSearchResponse,
    CallsListResponse,
    CallsSummaryResponse,
    CreatedResponse,
    DialRequest,
    DialResponse,
    DispositionRequest,
    EndRequest,
    NoteRequest,
)
from app.services import dialer_calls
from app.services.audit import record_user_activity

# The Calls module (the Web Dialer). Reading needs calls.view; placing calls and
# adding notes needs calls.create. The data is the production database's call log,
# the same one the standalone Web Dialer service shows.
router = APIRouter(prefix="/calls", tags=["calls"])

MAX_LIMIT = 200
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _parse_day(value: str | None) -> date | None:
    if value is None:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dates must be real calendar days (YYYY-MM-DD).",
        ) from exc


def _full_name(user: CCUser) -> str:
    return f"{user.first_name} {user.last_name}".strip()[:100]


# ---------------------------------------------------------------------
# Reading (calls.view)
# ---------------------------------------------------------------------


@router.get("/summary", response_model=CallsSummaryResponse)
def summary(
    start_date: str | None = Query(default=None, pattern=DATE_PATTERN),
    end_date: str | None = Query(default=None, pattern=DATE_PATTERN),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.view")),
) -> CallsSummaryResponse:
    del current_user

    first, last = calls_repository.resolve_days(_parse_day(start_date), _parse_day(end_date))
    begin, finish = calls_repository.day_range_utc(first, last)

    result = calls_repository.call_summary(prod_db, begin, finish)

    # The clinic days the numbers cover are shown back.
    return CallsSummaryResponse(
        start_date=first.isoformat(),
        end_date=last.isoformat(),
        **result,
    )


@router.get("/recent", response_model=CallsListResponse)
def recent_calls(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.view")),
) -> CallsListResponse:
    del current_user

    results = calls_repository.list_recent_calls(prod_db, limit)

    return CallsListResponse(count=len(results), results=results)


@router.get("/missed", response_model=CallsListResponse)
def missed_calls(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.view")),
) -> CallsListResponse:
    del current_user

    results = calls_repository.list_missed_calls(prod_db, limit)

    return CallsListResponse(count=len(results), results=results)


@router.get("/patients/search", response_model=CallPatientSearchResponse)
def search_patients(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.view")),
) -> CallPatientSearchResponse:
    del current_user

    results = calls_repository.search_patients(prod_db, q, limit)

    return CallPatientSearchResponse(query=q, count=len(results), results=results)


@router.get("/patients/call-history", response_model=CallPatientHistoryResponse)
def patient_call_history(
    phone: str = Query(min_length=3, max_length=40),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.view")),
) -> CallPatientHistoryResponse:
    del current_user

    return CallPatientHistoryResponse(
        phone=phone,
        history=calls_repository.patient_call_history(prod_db, phone, limit),
    )


# ---------------------------------------------------------------------
# Placing calls and notes (calls.create)
# ---------------------------------------------------------------------


@router.post("/dial", response_model=DialResponse)
def dial(
    payload: DialRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("calls.create")),
) -> DialResponse:
    """Logs a call and returns what the browser needs to start it: the call id, a
    short-lived Twilio token and the validated number. Twilio then asks the
    standalone Web Dialer service what to do with the call."""
    try:
        call_id, token, to_number = dialer_calls.start_call(
            db,
            current_user,
            number=payload.number,
            appointment_id=payload.appointment_id,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except dialer_calls.DialerCallError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return DialResponse(call_id=call_id, token=token, to=to_number)


@router.post("/{call_id}/ended", status_code=status.HTTP_204_NO_CONTENT)
def ended(
    call_id: int,
    payload: EndRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("calls.create")),
) -> None:
    """The browser reports its call over: it never reached Twilio (microphone
    refused, no connection), or it ended after `duration_s` seconds."""
    found = dialer_calls.end_call(
        db,
        call_id,
        current_user,
        reached_twilio=payload.reached_twilio,
        duration_s=payload.duration_s,
        ip_address=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found.")


def _require_call(prod_db: Session, call_log_id: int) -> None:
    if calls_repository.get_call_by_id(prod_db, call_log_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found.")


@router.post(
    "/{call_log_id}/notes",
    response_model=CreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_note(
    call_log_id: int,
    payload: NoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.create")),
) -> CreatedResponse:
    """Adds a note to a call (`call_log_id` is the id the call lists show)."""
    _require_call(prod_db, call_log_id)

    note_id = production_writes.insert_call_note(
        prod_db, call_log_id=call_log_id, note=payload.note, created_by=_full_name(current_user)
    )
    prod_db.commit()

    record_user_activity(
        db,
        actor_user_id=current_user.id,
        action="call.note_added",
        resource_type="call",
        resource_id=str(call_log_id),
        ip_address=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return CreatedResponse(id=note_id)


@router.post(
    "/{call_log_id}/disposition",
    response_model=CreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_disposition(
    call_log_id: int,
    payload: DispositionRequest,
    request: Request,
    db: Session = Depends(get_db),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("calls.create")),
) -> CreatedResponse:
    _require_call(prod_db, call_log_id)

    disposition_id = production_writes.insert_call_disposition(
        prod_db,
        call_log_id=call_log_id,
        disposition=payload.disposition,
        created_by=_full_name(current_user),
    )
    prod_db.commit()

    record_user_activity(
        db,
        actor_user_id=current_user.id,
        action="call.disposition_set",
        resource_type="call",
        resource_id=str(call_log_id),
        details={"disposition": payload.disposition},
        ip_address=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return CreatedResponse(id=disposition_id)
