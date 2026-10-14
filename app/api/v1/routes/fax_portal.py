from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies.fax_portal import request_ip, require_fax_portal_access
from app.db.session import get_db
from app.models.auth import CCUser
from app.repositories.auth import collect_role_codes
from app.schemas.fax_portal import (
    AgentResponse,
    AssigneeFilter,
    AssignReferralRequest,
    AssignReferralResponse,
    BoardResponse,
    FaxPortalCountsResponse,
    MoveReferralRequest,
    MoveReferralResponse,
)
from app.services.fax_portal_board import (
    board_changed_since,
    get_board,
    get_counts,
    list_agents,
)
from app.services.fax_portal_referrals import (
    MoveExtra,
    ReferralError,
    assign_referral,
    move_referral,
)

router = APIRouter(prefix="/fax-portal")


def _raise(db: Session, error: ReferralError) -> None:
    db.rollback()

    raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("/board", response_model=BoardResponse)
def get_board_route(
    assignee: AssigneeFilter = Query(default="all"),
    q: str | None = Query(default=None, max_length=100),
    show_older: str | None = Query(
        default=None,
        description="Comma-separated status codes whose older cards should be included.",
    ),
    since: datetime | None = Query(
        default=None,
        description="Server time from the previous response; returns changed=false when "
        "nothing has changed since.",
    ),
    since_header: datetime | None = Header(
        default=None,
        alias="X-Board-Since",
        description="Same as `since`, sent as a header. The browser app uses this so every "
        "poll has the same URL: a changing query string would make the browser repeat its "
        "CORS preflight (an extra OPTIONS request) on each poll.",
    ),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> BoardResponse:
    since = since or since_header

    if since is not None and not board_changed_since(db, since):
        return BoardResponse(changed=False, server_time=db.scalar(select(func.now())))

    older = {code.strip() for code in (show_older or "").split(",") if code.strip()}

    return get_board(db, user=current_user, assignee=assignee, q=q, show_older=older)


@router.post("/referrals/{referral_id}/move", response_model=MoveReferralResponse)
def move_referral_route(
    referral_id: int,
    payload: MoveReferralRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> MoveReferralResponse:
    try:
        result = move_referral(
            db,
            referral_id,
            payload.to_status,
            source=payload.source,
            user=current_user,
            role_codes=set(collect_role_codes(current_user)),
            expected_row_version=payload.row_version,
            extra=MoveExtra(
                reason=payload.reason,
                note=payload.note,
                acuity_appointment_id=payload.acuity_appointment_id,
                confirmed=payload.confirmed,
                callback_at=payload.callback_at,
            ),
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)

    db.commit()

    return MoveReferralResponse(
        id=result.referral.id,
        from_status=result.from_status,
        to_status=result.to_status,
        row_version=result.referral.row_version,
    )


@router.post("/referrals/{referral_id}/assign", response_model=AssignReferralResponse)
def assign_referral_route(
    referral_id: int,
    payload: AssignReferralRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> AssignReferralResponse:
    try:
        referral = assign_referral(
            db,
            referral_id,
            payload.assigned_user_id,
            user=current_user,
            role_codes=set(collect_role_codes(current_user)),
            expected_row_version=payload.row_version,
            ip_address=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ReferralError as error:
        _raise(db, error)

    db.commit()

    return AssignReferralResponse(
        id=referral.id,
        assigned_user_id=referral.assigned_user_id,
        row_version=referral.row_version,
    )


@router.get("/agents", response_model=list[AgentResponse])
def list_agents_route(
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> list[AgentResponse]:
    return list_agents(db)


@router.get("/counts", response_model=FaxPortalCountsResponse)
def get_counts_route(
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_fax_portal_access),
) -> FaxPortalCountsResponse:
    return get_counts(db, current_user)
