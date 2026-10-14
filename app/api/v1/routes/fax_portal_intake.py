from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies.fax_portal import request_ip, require_fax_portal_access
from app.db.session import get_db
from app.integrations.gcs_faxes import FaxPdfNotFoundError
from app.models.auth import CCUser
from app.models.fax_portal import CCFpAcuityAppointmentType, CCFpAcuityCalendar
from app.schemas.fax_portal import (
    AcuityAppointmentTypeResponse,
    AcuityCalendarResponse,
    AcuityRefreshResponse,
    AcuitySyncStatusResponse,
    IntakeDetailResponse,
    IntakeQueueResponse,
    IntakeUpdateRequest,
    IntakeUpdateResponse,
)
from app.services import fax_portal_acuity, fax_portal_intake
from app.services.fax_portal_referrals import ReferralError

router = APIRouter(prefix="/fax-portal")


def _raise(db: Session, error: ReferralError) -> None:
    db.rollback()

    raise HTTPException(status_code=error.status_code, detail=error.message) from error


# ---------------------------------------------------------------------
# Intake Review
# ---------------------------------------------------------------------


@router.get("/intake/queue", response_model=IntakeQueueResponse)
def get_intake_queue_route(
    duplicates_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=fax_portal_intake.DEFAULT_QUEUE_PAGE_SIZE, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> IntakeQueueResponse:
    return fax_portal_intake.get_intake_queue(
        db, duplicates_only=duplicates_only, page=page, page_size=page_size
    )


@router.get("/referrals/{referral_id}/intake", response_model=IntakeDetailResponse)
def get_intake_detail_route(
    referral_id: int,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> IntakeDetailResponse:
    try:
        return fax_portal_intake.get_intake_detail(db, referral_id)
    except ReferralError as error:
        _raise(db, error)
        raise  # unreachable; keeps the return type honest


@router.post("/referrals/{referral_id}/viewed", status_code=status.HTTP_204_NO_CONTENT)
def mark_viewed_route(
    referral_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> Response:
    try:
        fax_portal_intake.mark_viewed(
            db,
            referral_id,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)

    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/referrals/{referral_id}/intake", response_model=IntakeUpdateResponse)
def update_intake_route(
    referral_id: int,
    payload: IntakeUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> IntakeUpdateResponse:
    try:
        card, changed = fax_portal_intake.update_intake(
            db,
            referral_id,
            payload,
            user=current_user,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise

    db.commit()

    return IntakeUpdateResponse(id=card.id, row_version=card.row_version, changed_fields=changed)


@router.get("/faxes/{fax_id}/pdf")
def get_fax_pdf_route(
    fax_id: int,
    request: Request,
    referral_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> Response:
    """Streams the fax PDF through the backend (the bucket stays private).
    Authenticated, role-checked, never cached, and every view is audited."""
    try:
        data = fax_portal_intake.get_fax_pdf(
            db,
            fax_id,
            user=current_user,
            referral_id=referral_id,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)
        raise
    except FaxPdfNotFoundError as error:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The fax PDF is not available yet. It may still be arriving.",
        ) from error

    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="fax.pdf"',
        },
    )


# ---------------------------------------------------------------------
# Acuity reference data: clinics = calendars, exams = appointment types.
# Read from the synced cc_fp_acuity_* tables, never from Acuity live.
# ---------------------------------------------------------------------


@router.get("/acuity/calendars", response_model=list[AcuityCalendarResponse])
def list_acuity_calendars_route(
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[AcuityCalendarResponse]:
    return [
        AcuityCalendarResponse(
            acuity_calendar_id=c.acuity_calendar_id,
            name=c.name,
            location=c.location,
            timezone=c.timezone,
        )
        for c in fax_portal_acuity.list_active_calendars(db)
    ]


@router.get("/acuity/appointment-types", response_model=list[AcuityAppointmentTypeResponse])
def list_acuity_types_route(
    calendar_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[AcuityAppointmentTypeResponse]:
    return [
        AcuityAppointmentTypeResponse(
            acuity_appointment_type_id=t.acuity_appointment_type_id,
            name=t.name,
            category=t.category,
            duration_min=t.duration_min,
            price=str(t.price) if t.price is not None else None,
            available_at_calendar=available,
        )
        for t, available in fax_portal_acuity.list_active_types(db, calendar_id)
    ]


@router.get("/acuity/status", response_model=AcuitySyncStatusResponse)
def get_acuity_status_route(
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> AcuitySyncStatusResponse:
    last = fax_portal_acuity.last_sync_run(db)
    last_ok = db.scalar(
        select(func.max(fax_portal_acuity.CCFpAcuitySyncRun.finished_at)).where(
            fax_portal_acuity.CCFpAcuitySyncRun.status == "success"
        )
    )

    return AcuitySyncStatusResponse(
        last_run_at=last_ok,
        last_status=last.status if last else None,
        last_error=last.error_message if last and last.status == "failed" else None,
        calendars=db.scalar(
            select(func.count())
            .select_from(CCFpAcuityCalendar)
            .where(CCFpAcuityCalendar.is_active.is_(True))
        )
        or 0,
        appointment_types=db.scalar(
            select(func.count())
            .select_from(CCFpAcuityAppointmentType)
            .where(CCFpAcuityAppointmentType.is_active.is_(True))
        )
        or 0,
    )


@router.post("/acuity/refresh", response_model=AcuityRefreshResponse)
def refresh_acuity_route(
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> AcuityRefreshResponse:
    """The "Refresh from Acuity" button: runs the same sync as the hourly job."""
    try:
        run = fax_portal_acuity.run_acuity_sync(
            db, trigger="manual", triggered_by_user_id=current_user.id
        )
    except fax_portal_acuity.SyncAlreadyRunningError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A refresh from Acuity is already running. Try again in a moment.",
        ) from error

    if run.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not refresh from Acuity: {run.error_message}",
        )

    return AcuityRefreshResponse(
        status=run.status,
        calendars_added=run.calendars_added,
        calendars_updated=run.calendars_updated,
        calendars_removed=run.calendars_removed,
        types_added=run.types_added,
        types_updated=run.types_updated,
        types_removed=run.types_removed,
    )
