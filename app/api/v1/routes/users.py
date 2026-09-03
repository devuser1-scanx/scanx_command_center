from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
    status,
)
from sqlalchemy.orm import Session

from app.api.dependencies.auth import (
    require_permission,
)
from app.api.v1.routes.auth import (
    get_client_ip,
    get_user_agent,
)
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.auth import MessageResponse
from app.schemas.users import (
    UserClinicAssignmentRequest,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserRoleAssignmentRequest,
    UserStatusRequest,
    UserUpdateRequest,
)
from app.services.users import (
    admin_reset_password,
    assign_clinics,
    assign_roles,
    build_user_response,
    create_user,
    get_user_or_404,
    get_users,
    set_user_status,
    unlock_user,
    update_user,
)

router = APIRouter(
    prefix="/users",
)


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_command_center_user(
    payload: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.create")),
) -> UserResponse:
    return create_user(
        db,
        payload=payload,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "",
    response_model=UserListResponse,
)
def list_command_center_users(
    limit: int = Query(
        default=25,
        ge=1,
        le=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    search: str | None = Query(
        default=None,
        max_length=200,
    ),
    is_active: bool | None = Query(
        default=None,
    ),
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.view")),
) -> UserListResponse:
    del current_user

    return get_users(
        db,
        limit=limit,
        offset=offset,
        search=search,
        is_active=is_active,
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
)
def get_command_center_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.view")),
) -> UserResponse:
    del current_user

    return build_user_response(get_user_or_404(db, user_id))


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
)
def update_command_center_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.update")),
) -> UserResponse:
    return update_user(
        db,
        user_id=user_id,
        payload=payload,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.put(
    "/{user_id}/status",
    response_model=UserResponse,
)
def update_command_center_user_status(
    user_id: int,
    payload: UserStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.deactivate")),
) -> UserResponse:
    return set_user_status(
        db,
        user_id=user_id,
        is_active=payload.is_active,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/{user_id}/unlock",
    response_model=UserResponse,
)
def unlock_command_center_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.update")),
) -> UserResponse:
    return unlock_user(
        db,
        user_id=user_id,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.put(
    "/{user_id}/roles",
    response_model=UserResponse,
)
def update_command_center_user_roles(
    user_id: int,
    payload: UserRoleAssignmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.assign_role")),
) -> UserResponse:
    return assign_roles(
        db,
        user_id=user_id,
        role_codes=payload.role_codes,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.put(
    "/{user_id}/clinics",
    response_model=UserResponse,
)
def update_command_center_user_clinics(
    user_id: int,
    payload: UserClinicAssignmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.assign_clinic")),
) -> UserResponse:
    return assign_clinics(
        db,
        user_id=user_id,
        clinic_ids=payload.clinic_ids,
        primary_clinic_id=payload.primary_clinic_id,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/{user_id}/reset-password",
    response_model=MessageResponse,
)
def reset_user_password_as_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("users.update")),
) -> MessageResponse:
    admin_reset_password(
        db,
        user_id=user_id,
        actor_user=current_user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

    return MessageResponse(
        message=("A new temporary password has been emailed to the user.")
    )
