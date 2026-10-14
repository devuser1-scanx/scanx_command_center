from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.fax_portal import CCFpActivityLog


def log_activity(
    db: Session,
    *,
    section: str,
    action: str,
    user_id: int | None = None,
    referral_id: int | None = None,
    fax_id: int | None = None,
    changes: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> CCFpActivityLog:
    """Appends one row to cc_fp_activity_log (who did what, in which section).

    user_id None means the system / a job. Callers must keep PHI (names, DOB,
    phone numbers, payload text) out of `details`; `changes` may hold
    before/after values of edited fields since this table is itself PHI-grade
    storage, but it must never be written to stdout or Cloud Logging.

    Added to the caller's session; the caller commits, so the entry lands in
    the same transaction as the change it records.
    """
    entry = CCFpActivityLog(
        section=section,
        action=action,
        user_id=user_id,
        referral_id=referral_id,
        fax_id=fax_id,
        changes=changes,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.add(entry)

    return entry
