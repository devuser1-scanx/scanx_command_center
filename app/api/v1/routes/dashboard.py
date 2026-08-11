from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.db.prod_session import get_prod_db
from app.models.auth import CCUser
from app.schemas.dashboard import (
    DashboardSummaryResponse,
    DashboardTimelineResponse,
)
from app.services.dashboard import get_dashboard_summary, get_dashboard_timeline

router = APIRouter(prefix="/dashboard")


@router.get("/timeline", response_model=DashboardTimelineResponse)
def get_dashboard_timeline_route(
    clinic_id: int | None = Query(default=None),
    day: date | None = Query(default=None, alias="date"),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("dashboard.view")),
) -> DashboardTimelineResponse:
    return get_dashboard_timeline(
        prod_db,
        clinic_id=clinic_id,
        day=day or date.today(),
    )


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary_route(
    day: date | None = Query(default=None, alias="date"),
    prod_db: Session = Depends(get_prod_db),
    current_user: CCUser = Depends(require_permission("dashboard.view")),
) -> DashboardSummaryResponse:
    return get_dashboard_summary(prod_db, day=day or date.today())
