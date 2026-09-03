from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    clinics,
    dashboard,
    health,
    patients,
    reports,
    roles,
    users,
    ws_dashboard,
)

api_router = APIRouter()

api_router.include_router(
    health.router,
    tags=["health"],
)

api_router.include_router(
    auth.router,
    tags=["authentication"],
)

api_router.include_router(
    users.router,
    tags=["user management"],
)

api_router.include_router(
    clinics.router,
    tags=["clinics"],
)

api_router.include_router(
    dashboard.router,
    tags=["dashboard"],
)

api_router.include_router(
    ws_dashboard.router,
    tags=["dashboard"],
)

api_router.include_router(
    patients.router,
    tags=["patients"],
)

api_router.include_router(
    reports.router,
    tags=["reports"],
)

api_router.include_router(
    roles.router,
    tags=["roles"],
)
