from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    hash_password,
    utc_now,
    validate_password_strength,
)
from app.models.auth import CCUser
from app.repositories.auth import revoke_all_user_sessions
from app.repositories.users import (
    get_roles_by_codes,
    get_user_by_email,
    get_user_by_id,
    list_users,
    normalize_email,
    replace_user_clinic_access,
    replace_user_roles,
)
from app.schemas.users import (
    UserClinicItem,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserRoleItem,
    UserUpdateRequest,
)
from app.services.auth import record_user_activity


def build_user_response(
    user: CCUser,
) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        is_active=user.is_active,
        is_email_verified=user.is_email_verified,
        must_change_password=user.must_change_password,
        failed_login_attempts=user.failed_login_attempts,
        locked_until=user.locked_until,
        last_login_at=user.last_login_at,
        password_changed_at=user.password_changed_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        roles=[
            UserRoleItem(
                id=user_role.role.id,
                code=user_role.role.code,
                name=user_role.role.name,
            )
            for user_role in sorted(
                user.roles,
                key=lambda item: item.role.code,
            )
        ],
        clinic_access=[
            UserClinicItem(
                clinic_id=access.clinic_id,
                is_primary=access.is_primary,
            )
            for access in sorted(
                user.clinic_access,
                key=lambda item: (
                    not item.is_primary,
                    item.clinic_id,
                ),
            )
        ],
    )


def create_user(
    db: Session,
    *,
    payload: UserCreateRequest,
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> UserResponse:
    normalized_email = normalize_email(
        str(payload.email)
    )

    if get_user_by_email(db, normalized_email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    try:
        validate_password_strength(
            payload.temporary_password
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    roles = get_roles_by_codes(
        db,
        payload.role_codes,
    )

    requested_role_codes = {
        code.strip().lower()
        for code in payload.role_codes
        if code.strip()
    }
    found_role_codes = {
        role.code
        for role in roles
    }

    missing_roles = (
        requested_role_codes - found_role_codes
    )

    if missing_roles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "One or more roles are invalid.",
                "invalid_roles": sorted(missing_roles),
            },
        )

    if (
        payload.primary_clinic_id is not None
        and payload.primary_clinic_id
        not in payload.clinic_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "primary_clinic_id must be included "
                "in clinic_ids."
            ),
        )

    user = CCUser(
        email=normalized_email,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=(
            payload.phone.strip()
            if payload.phone
            else None
        ),
        password_hash=hash_password(
            payload.temporary_password
        ),
        is_active=True,
        is_email_verified=True,
        must_change_password=True,
        created_by_user_id=actor_user.id,
        updated_by_user_id=actor_user.id,
    )

    db.add(user)
    db.flush()

    replace_user_roles(
        db,
        user=user,
        roles=roles,
        assigned_by_user_id=actor_user.id,
    )

    replace_user_clinic_access(
        db,
        user=user,
        clinic_ids=payload.clinic_ids,
        primary_clinic_id=payload.primary_clinic_id,
        assigned_by_user_id=actor_user.id,
    )

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action="users.created",
        resource_type="cc_user",
        resource_id=str(user.id),
        details={
            "email": user.email,
            "roles": sorted(found_role_codes),
            "clinic_ids": sorted(
                set(payload.clinic_ids)
            ),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The user could not be created because "
                "of a conflicting record."
            ),
        ) from exc

    created_user = get_user_by_id(db, user.id)

    if created_user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Created user could not be loaded.",
        )

    return build_user_response(created_user)


def get_user_or_404(
    db: Session,
    user_id: int,
) -> CCUser:
    user = get_user_by_id(db, user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return user


def get_users(
    db: Session,
    *,
    limit: int,
    offset: int,
    search: str | None,
    is_active: bool | None,
) -> UserListResponse:
    users, total = list_users(
        db,
        limit=limit,
        offset=offset,
        search=search,
        is_active=is_active,
    )

    return UserListResponse(
        items=[
            build_user_response(user)
            for user in users
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def update_user(
    db: Session,
    *,
    user_id: int,
    payload: UserUpdateRequest,
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> UserResponse:
    user = get_user_or_404(db, user_id)

    changes = payload.model_dump(
        exclude_unset=True
    )

    if "first_name" in changes:
        user.first_name = changes[
            "first_name"
        ].strip()

    if "last_name" in changes:
        user.last_name = changes[
            "last_name"
        ].strip()

    if "phone" in changes:
        phone = changes["phone"]
        user.phone = (
            phone.strip()
            if phone
            else None
        )

    user.updated_by_user_id = actor_user.id

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action="users.updated",
        resource_type="cc_user",
        resource_id=str(user.id),
        details={"updated_fields": sorted(changes)},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    updated_user = get_user_or_404(
        db,
        user.id,
    )

    return build_user_response(updated_user)


def set_user_status(
    db: Session,
    *,
    user_id: int,
    is_active: bool,
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> UserResponse:
    user = get_user_or_404(db, user_id)

    if (
        user.id == actor_user.id
        and not is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "You cannot deactivate your own account."
            ),
        )

    user.is_active = is_active
    user.updated_by_user_id = actor_user.id

    if not is_active:
        revoke_all_user_sessions(
            db,
            user_id=user.id,
            revoked_at=utc_now(),
        )

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action=(
            "users.activated"
            if is_active
            else "users.deactivated"
        ),
        resource_type="cc_user",
        resource_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    return build_user_response(
        get_user_or_404(db, user.id)
    )


def unlock_user(
    db: Session,
    *,
    user_id: int,
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> UserResponse:
    user = get_user_or_404(db, user_id)

    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_by_user_id = actor_user.id

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action="users.unlocked",
        resource_type="cc_user",
        resource_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    return build_user_response(
        get_user_or_404(db, user.id)
    )


def assign_roles(
    db: Session,
    *,
    user_id: int,
    role_codes: list[str],
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> UserResponse:
    user = get_user_or_404(db, user_id)

    roles = get_roles_by_codes(
        db,
        role_codes,
    )

    requested = {
        code.strip().lower()
        for code in role_codes
        if code.strip()
    }
    found = {
        role.code
        for role in roles
    }

    missing = requested - found

    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "One or more roles are invalid.",
                "invalid_roles": sorted(missing),
            },
        )

    if (
        user.id == actor_user.id
        and "admin" not in found
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "You cannot remove your own admin role."
            ),
        )

    replace_user_roles(
        db,
        user=user,
        roles=roles,
        assigned_by_user_id=actor_user.id,
    )

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action="users.roles_updated",
        resource_type="cc_user",
        resource_id=str(user.id),
        details={"roles": sorted(found)},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    return build_user_response(
        get_user_or_404(db, user.id)
    )


def assign_clinics(
    db: Session,
    *,
    user_id: int,
    clinic_ids: list[int],
    primary_clinic_id: int | None,
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> UserResponse:
    user = get_user_or_404(db, user_id)

    if (
        primary_clinic_id is not None
        and primary_clinic_id not in clinic_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "primary_clinic_id must be included "
                "in clinic_ids."
            ),
        )

    replace_user_clinic_access(
        db,
        user=user,
        clinic_ids=clinic_ids,
        primary_clinic_id=primary_clinic_id,
        assigned_by_user_id=actor_user.id,
    )

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action="users.clinics_updated",
        resource_type="cc_user",
        resource_id=str(user.id),
        details={
            "clinic_ids": sorted(set(clinic_ids)),
            "primary_clinic_id": primary_clinic_id,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    return build_user_response(
        get_user_or_404(db, user.id)
    )


def admin_reset_password(
    db: Session,
    *,
    user_id: int,
    temporary_password: str,
    actor_user: CCUser,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    user = get_user_or_404(db, user_id)

    try:
        validate_password_strength(
            temporary_password
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    now = utc_now()

    user.password_hash = hash_password(
        temporary_password
    )
    user.must_change_password = True
    user.password_changed_at = now
    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_by_user_id = actor_user.id

    revoke_all_user_sessions(
        db,
        user_id=user.id,
        revoked_at=now,
    )

    record_user_activity(
        db,
        actor_user_id=actor_user.id,
        target_user_id=user.id,
        action="users.password_reset_by_admin",
        resource_type="cc_user",
        resource_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()