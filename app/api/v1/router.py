from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    calls,
    clinics,
    dashboard,
    fax_portal,
    fax_portal_case,
    fax_portal_intake,
    fax_portal_twilio_webhooks,
    fax_portal_webhooks,
    health,
    inbox,
    patients,
    reports,
    roles,
    users,
    ws_calls,
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

api_router.include_router(
    fax_portal_webhooks.router,
    tags=["fax portal"],
)

api_router.include_router(
    fax_portal.router,
    tags=["fax portal"],
)

api_router.include_router(
    fax_portal_intake.router,
    tags=["fax portal"],
)

api_router.include_router(
    fax_portal_case.router,
    tags=["fax portal"],
)

api_router.include_router(
    fax_portal_twilio_webhooks.router,
    tags=["fax portal"],
)

api_router.include_router(
    calls.router,
    tags=["calls"],
)

api_router.include_router(
    ws_calls.router,
    tags=["calls"],
)

api_router.include_router(
    inbox.router,
    tags=["inbox"],
)
