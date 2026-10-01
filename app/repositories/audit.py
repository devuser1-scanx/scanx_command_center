from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.auth import CCUserActivityAudit


def create_user_activity_audit(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    clinic_id: int | None = None,
    target_user_id: int | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CCUserActivityAudit:
    audit = CCUserActivityAudit(
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        clinic_id=clinic_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.add(audit)
    db.flush()

    return audit
