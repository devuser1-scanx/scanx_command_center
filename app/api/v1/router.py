from fastapi import APIRouter

from app.api.v1.routes import auth, health, users

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