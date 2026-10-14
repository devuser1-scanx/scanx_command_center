from __future__ import annotations

import logging
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.integrations import acuity_client
from app.models.fax_portal import (
    CCFpAcuityAppointmentType,
    CCFpAcuityAppointmentTypeCalendar,
    CCFpAcuityCalendar,
    CCFpAcuitySyncRun,
)

# Never log Acuity responses or the API key here: only ids and counts.
logger = logging.getLogger(__name__)

# Stable key for the advisory lock that keeps the hourly job and the
# "Refresh from Acuity" button from overlapping.
SYNC_LOCK_KEY = 804_113_001

MAX_RECORDED_CHANGES = 200

Fetcher = Callable[[], list[dict[str, Any]]]


class SyncAlreadyRunningError(Exception):
    pass


class SyncRefusedError(Exception):
    """Acuity's answer looks wrong (e.g. empty), so nothing was changed."""


def _text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _price(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_LOCATION_SUFFIX = re.compile(r"\s+location\s*$", re.IGNORECASE)


def clinic_display_name(name: str) -> str:
    """Acuity calendars are named like "Dallas Location"; the clinic is shown as
    "Dallas". The full Acuity response stays in `raw`."""
    stripped = _LOCATION_SUFFIX.sub("", name).strip()

    return stripped or name


def _calendar_values(item: dict[str, Any]) -> dict[str, Any]:
    name = _text(item.get("name")) or f"Calendar {item.get('id')}"

    return {
        "name": clinic_display_name(name)[:150],
        "location": _text(item.get("location")),
        "timezone": (_text(item.get("timezone")) or "")[:50] or None,
        "email": (_text(item.get("email")) or "")[:255] or None,
        "description": _text(item.get("description")),
    }


def _type_values(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": (_text(item.get("name")) or f"Appointment type {item.get('id')}")[:255],
        "category": (_text(item.get("category")) or "")[:150] or None,
        "duration_min": _int(item.get("duration")),
        "price": _price(item.get("price")),
        "description": _text(item.get("description")),
        "scheduling_url": _text(item.get("schedulingUrl")),
        "is_private": item.get("private") if isinstance(item.get("private"), bool) else None,
    }


def run_acuity_sync(
    db: Session,
    *,
    trigger: str,
    triggered_by_user_id: int | None = None,
    fetch_calendars: Fetcher | None = None,
    fetch_types: Fetcher | None = None,
) -> CCFpAcuitySyncRun:
    """Copies Acuity's calendars (clinics) and appointment types (exams) into
    cc_fp_acuity_*, matching on the Acuity id:

      new id          -> insert
      existing id     -> update changed values in place (renames included)
      id not returned -> is_active = FALSE, removed_at = now()   (never deleted)

    so cards that point at a clinic or exam stay valid. Each run is recorded in
    cc_fp_acuity_sync_run. Raises SyncAlreadyRunningError if another run holds
    the advisory lock. On failure nothing is changed and a failed run row is
    recorded instead.
    """
    run: CCFpAcuitySyncRun

    try:
        got_lock = db.scalar(select(func.pg_try_advisory_xact_lock(SYNC_LOCK_KEY)))

        if not got_lock:
            raise SyncAlreadyRunningError("An Acuity sync is already running.")

        run = CCFpAcuitySyncRun(trigger=trigger, triggered_by_user_id=triggered_by_user_id)
        db.add(run)
        db.flush()

        calendars = (fetch_calendars or acuity_client.list_calendars)()
        types = (fetch_types or acuity_client.list_appointment_types)()

        _guard_against_empty_answer(db, calendars, types)

        changes: list[dict[str, Any]] = []
        _sync_calendars(db, run, calendars, changes)
        _sync_types(db, run, types, changes)
        _rebuild_type_calendars(db, types)

        run.status = "success"
        run.finished_at = func.now()
        run.changes = changes[:MAX_RECORDED_CHANGES] or None

        db.commit()

        return run

    except SyncAlreadyRunningError:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        # Class name only in the log; the message is kept on the run row.
        logger.error("Acuity sync failed (%s).", type(exc).__name__)

        failed = CCFpAcuitySyncRun(
            trigger=trigger,
            triggered_by_user_id=triggered_by_user_id,
            status="failed",
            finished_at=func.now(),
            error_message=_error_message(exc),
        )
        db.add(failed)
        db.commit()

        return failed


def _error_message(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__

    return str(message)[:1000]


def _guard_against_empty_answer(
    db: Session, calendars: list[dict[str, Any]], types: list[dict[str, Any]]
) -> None:
    """An empty list while we hold active rows is far more likely an outage or
    a bad key than a business that closed every clinic. Refuse rather than
    deactivate everything and empty the dropdowns."""
    if not calendars and db.scalar(
        select(CCFpAcuityCalendar.acuity_calendar_id)
        .where(CCFpAcuityCalendar.is_active.is_(True))
        .limit(1)
    ):
        raise SyncRefusedError("Acuity returned no calendars; nothing was changed.")

    if not types and db.scalar(
        select(CCFpAcuityAppointmentType.acuity_appointment_type_id)
        .where(CCFpAcuityAppointmentType.is_active.is_(True))
        .limit(1)
    ):
        raise SyncRefusedError("Acuity returned no appointment types; nothing was changed.")


def _sync_calendars(
    db: Session,
    run: CCFpAcuitySyncRun,
    items: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> None:
    existing = {row.acuity_calendar_id: row for row in db.scalars(select(CCFpAcuityCalendar))}
    seen: set[int] = set()

    for item in items:
        calendar_id = _int(item.get("id"))

        if calendar_id is None:
            continue

        seen.add(calendar_id)
        values = _calendar_values(item)
        row = existing.get(calendar_id)

        if row is None:
            db.add(CCFpAcuityCalendar(acuity_calendar_id=calendar_id, raw=item, **values))
            run.calendars_added += 1
            continue

        changed = _apply(row, values, "calendar", calendar_id, changes)
        reactivated = not row.is_active

        if reactivated:
            row.is_active = True
            row.removed_at = None

        row.raw = item
        row.last_synced_at = func.now()

        if changed or reactivated:
            row.updated_at = func.now()
            run.calendars_updated += 1

    for calendar_id, row in existing.items():
        if calendar_id not in seen and row.is_active:
            row.is_active = False
            row.removed_at = func.now()
            row.updated_at = func.now()
            run.calendars_removed += 1

    db.flush()


def _sync_types(
    db: Session,
    run: CCFpAcuitySyncRun,
    items: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> None:
    existing = {
        row.acuity_appointment_type_id: row for row in db.scalars(select(CCFpAcuityAppointmentType))
    }
    seen: set[int] = set()

    for item in items:
        type_id = _int(item.get("id"))

        if type_id is None:
            continue

        seen.add(type_id)
        values = _type_values(item)
        active = item.get("active", True) is not False
        row = existing.get(type_id)

        if row is None:
            db.add(
                CCFpAcuityAppointmentType(
                    acuity_appointment_type_id=type_id,
                    raw=item,
                    is_active=active,
                    removed_at=None if active else func.now(),
                    **values,
                )
            )
            run.types_added += 1
            continue

        changed = _apply(row, values, "type", type_id, changes)

        if active and not row.is_active:
            row.is_active = True
            row.removed_at = None
            changed = True
        elif not active and row.is_active:
            row.is_active = False
            row.removed_at = func.now()
            run.types_removed += 1
            changed = True

        row.raw = item
        row.last_synced_at = func.now()

        if changed:
            row.updated_at = func.now()
            run.types_updated += 1

    for type_id, row in existing.items():
        if type_id not in seen and row.is_active:
            row.is_active = False
            row.removed_at = func.now()
            row.updated_at = func.now()
            run.types_removed += 1

    db.flush()


def _apply(
    row: Any,
    values: dict[str, Any],
    kind: str,
    item_id: int,
    changes: list[dict[str, Any]],
) -> bool:
    changed = False

    for field, new in values.items():
        old = getattr(row, field)

        if old != new:
            setattr(row, field, new)
            changed = True
            changes.append(
                {
                    "kind": kind,
                    "id": item_id,
                    "field": field,
                    "from": str(old) if old is not None else None,
                    "to": str(new) if new is not None else None,
                }
            )

    return changed


def _rebuild_type_calendars(db: Session, types: list[dict[str, Any]]) -> None:
    """Replaces, for each type Acuity returned, the clinics that offer it."""
    known_calendars = set(db.scalars(select(CCFpAcuityCalendar.acuity_calendar_id)))
    type_ids = [t for t in (_int(item.get("id")) for item in types) if t is not None]

    if not type_ids:
        return

    db.execute(
        delete(CCFpAcuityAppointmentTypeCalendar).where(
            CCFpAcuityAppointmentTypeCalendar.acuity_appointment_type_id.in_(type_ids)
        )
    )

    for item in types:
        type_id = _int(item.get("id"))

        if type_id is None:
            continue

        calendar_ids = item.get("calendarIDs")

        for calendar_id in {_int(c) for c in calendar_ids or []}:
            # A calendar we do not hold (e.g. private) cannot be referenced.
            if calendar_id in known_calendars:
                db.add(
                    CCFpAcuityAppointmentTypeCalendar(
                        acuity_appointment_type_id=type_id,
                        acuity_calendar_id=calendar_id,
                    )
                )

    db.flush()


# ---------------------------------------------------------------------
# Reads for the UI (dropdowns read these tables, never Acuity live)
# ---------------------------------------------------------------------


def list_active_calendars(db: Session) -> list[CCFpAcuityCalendar]:
    return list(
        db.scalars(
            select(CCFpAcuityCalendar)
            .where(CCFpAcuityCalendar.is_active.is_(True))
            .order_by(CCFpAcuityCalendar.name)
        )
    )


def list_active_types(
    db: Session, calendar_id: int | None = None
) -> list[tuple[CCFpAcuityAppointmentType, bool]]:
    """Active exams, each flagged whether the chosen clinic offers it. A type
    with no clinic mapping at all is treated as available everywhere."""
    types = list(
        db.scalars(
            select(CCFpAcuityAppointmentType)
            .where(CCFpAcuityAppointmentType.is_active.is_(True))
            .order_by(CCFpAcuityAppointmentType.name)
        )
    )

    if calendar_id is None:
        return [(t, True) for t in types]

    mapping: dict[int, set[int]] = {}

    for type_id, cal_id in db.execute(
        select(
            CCFpAcuityAppointmentTypeCalendar.acuity_appointment_type_id,
            CCFpAcuityAppointmentTypeCalendar.acuity_calendar_id,
        )
    ):
        mapping.setdefault(type_id, set()).add(cal_id)

    return [
        (
            t,
            t.acuity_appointment_type_id not in mapping
            or calendar_id in mapping[t.acuity_appointment_type_id],
        )
        for t in types
    ]


def last_sync_run(db: Session) -> CCFpAcuitySyncRun | None:
    return db.scalar(select(CCFpAcuitySyncRun).order_by(CCFpAcuitySyncRun.id.desc()).limit(1))


# ---------------------------------------------------------------------
# One-time seeding
# ---------------------------------------------------------------------


class SeedOutcome:
    SEEDED = "seeded"
    ALREADY_SEEDED = "already_seeded"
    ALREADY_RUNNING = "already_running"
    FAILED = "failed"


def seed_acuity_once(db: Session) -> tuple[str, CCFpAcuitySyncRun | None]:
    """Loads the clinics and exams from Acuity the first time, and does nothing
    after that.

    "Already done" is read from the database itself: any successful row in
    cc_fp_acuity_sync_run (from this seeder, the hourly job or the Refresh
    button) means the tables are seeded. A failed run does not count, so a
    failed attempt can simply be run again. No extra table or flag is needed,
    and a run on a different environment's database seeds that one.

    Returns (outcome, the run that did the work or the earlier successful run).
    """
    earlier = db.scalar(
        select(CCFpAcuitySyncRun)
        .where(CCFpAcuitySyncRun.status == "success")
        .order_by(CCFpAcuitySyncRun.id.asc())
        .limit(1)
    )

    if earlier is not None:
        return SeedOutcome.ALREADY_SEEDED, earlier

    try:
        run = run_acuity_sync(db, trigger="manual")
    except SyncAlreadyRunningError:
        return SeedOutcome.ALREADY_RUNNING, None

    return (SeedOutcome.SEEDED if run.status == "success" else SeedOutcome.FAILED), run
