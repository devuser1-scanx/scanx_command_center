from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.time_utils import FALLBACK_TIMEZONE, day_bounds_utc

# Reads for the Calls module (the Web Dialer) from the production database. The
# same queries the standalone Web Dialer service runs, so both show the same
# calls: one `call_logs` row is one call, matched to at most one appointment
# (the exact appointment id first, otherwise the latest with the same phone).
#
# `call_logs` and `appointment` timestamps are `timestamp without time zone`
# holding UTC; they are returned as UTC-aware values.

MISSED_STATUSES = ("no-answer", "busy", "failed", "canceled", "cancelled")

_DIGITS_ONLY = (
    "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE({column}, ''), '+', ''),"
    " '-', ''), ' ', ''), '(', ''), ')', '')"
)

_CALL_SELECT = f"""
    SELECT
        cl.id, cl.appointment_id, cl.staff_phone, cl.patient_number, cl.direction,
        cl.call_sid, cl.status, cl.duration, cl.created_at, cl.updated_at,
        a.first_name, a.last_name,
        CONCAT_WS(' ', a.first_name, a.last_name) AS patient_name,
        a.email, a.appointment_type, a.clinic_id, a.location, a.calendar,
        c.name AS clinic_name
    FROM call_logs cl
    LEFT JOIN LATERAL (
        SELECT ap.*
        FROM appointment ap
        WHERE
            ap.appointment_id = cl.appointment_id
            OR (
                cl.patient_number IS NOT NULL
                AND {_DIGITS_ONLY.format(column="ap.phone")}
                    = {_DIGITS_ONLY.format(column="cl.patient_number")}
            )
        ORDER BY
            CASE WHEN ap.appointment_id = cl.appointment_id THEN 0 ELSE 1 END,
            COALESCE(ap.appointment_datetime, ap.date) DESC NULLS LAST,
            ap.created_at DESC NULLS LAST
        LIMIT 1
    ) a ON TRUE
    LEFT JOIN clinics c ON c.id = a.clinic_id
"""

_MISSED_FILTER = "LOWER(COALESCE(cl.status, '')) IN :missed"

_DAY_FILTER = "cl.created_at BETWEEN :start AND :end"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _text_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def format_call_row(row: dict[str, Any]) -> dict[str, Any]:
    patient_name = (row.get("patient_name") or "").strip()

    return {
        **row,
        "created_at": _utc(row.get("created_at")),
        "updated_at": _utc(row.get("updated_at")),
        "display_name": patient_name or "Unknown Caller",
        "is_known_patient": bool(patient_name),
        "is_missed": (row.get("status") or "").lower() in MISSED_STATUSES,
    }


def _in_clause(statement: str) -> Any:
    """A statement using "IN :missed": the list of missed statuses is bound (and
    expanded into one placeholder each) automatically."""
    return text(statement).bindparams(
        bindparam("missed", value=list(MISSED_STATUSES), expanding=True)
    )


def resolve_days(start: date | None, end: date | None) -> tuple[date, date]:
    """The clinic days a summary covers. No start: today; no end: the start day
    only; an end before the start is read as the start day."""
    first = start or datetime.now(ZoneInfo(FALLBACK_TIMEZONE)).date()

    return first, max(first, end or first)


def day_range_utc(first: date, last: date) -> tuple[datetime, datetime]:
    """The naive-UTC range covering the clinic days first..last."""
    begin, _ = day_bounds_utc(first, FALLBACK_TIMEZONE)
    _, finish = day_bounds_utc(last, FALLBACK_TIMEZONE)

    return begin.replace(tzinfo=None), finish.replace(tzinfo=None)


def list_recent_calls(prod_db: Session, limit: int) -> list[dict[str, Any]]:
    rows = (
        prod_db.execute(
            text(f"{_CALL_SELECT} ORDER BY cl.created_at DESC LIMIT :limit"),
            {"limit": limit},
        )
        .mappings()
        .all()
    )

    return [format_call_row(dict(row)) for row in rows]


def list_missed_calls(prod_db: Session, limit: int) -> list[dict[str, Any]]:
    rows = (
        prod_db.execute(
            _in_clause(
                f"{_CALL_SELECT} WHERE {_MISSED_FILTER} ORDER BY cl.created_at DESC LIMIT :limit"
            ),
            {"limit": limit},
        )
        .mappings()
        .all()
    )

    return [format_call_row(dict(row)) for row in rows]


def get_call_by_id(prod_db: Session, call_log_id: int) -> dict[str, Any] | None:
    row = (
        prod_db.execute(
            text(f"{_CALL_SELECT} WHERE cl.id = :call_log_id LIMIT 1"),
            {"call_log_id": call_log_id},
        )
        .mappings()
        .first()
    )

    return format_call_row(dict(row)) if row else None


def call_summary(prod_db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    """Counts and the ten latest recent / missed calls between two naive-UTC
    instants."""
    params = {"start": start, "end": end}

    counts = (
        prod_db.execute(
            _in_clause(
                f"""
                SELECT
                    COUNT(*) AS total_calls,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(cl.direction, '')) = 'outbound'
                    ) AS outgoing_calls,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(cl.direction, '')) = 'inbound'
                    ) AS incoming_calls,
                    COUNT(*) FILTER (WHERE {_MISSED_FILTER}) AS missed_calls,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(cl.status, '')) = 'completed'
                    ) AS completed_calls,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(cl.status, '')) IN ('failed', 'busy')
                    ) AS failed_or_busy_calls,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(cl.status, '')) = 'no-answer'
                    ) AS no_answer_calls,
                    COALESCE(ROUND(AVG(NULLIF(cl.duration, 0))), 0) AS average_duration_seconds
                FROM call_logs cl
                WHERE {_DAY_FILTER}
                """
            ),
            params,
        )
        .mappings()
        .one()
    )

    recent = (
        prod_db.execute(
            text(f"{_CALL_SELECT} WHERE {_DAY_FILTER} ORDER BY cl.created_at DESC LIMIT 10"),
            params,
        )
        .mappings()
        .all()
    )

    missed = (
        prod_db.execute(
            _in_clause(
                f"{_CALL_SELECT} WHERE {_DAY_FILTER} AND {_MISSED_FILTER} "
                "ORDER BY cl.created_at DESC LIMIT 10"
            ),
            params,
        )
        .mappings()
        .all()
    )

    return {
        "total_calls": int(counts["total_calls"] or 0),
        "outgoing_calls": int(counts["outgoing_calls"] or 0),
        "incoming_calls": int(counts["incoming_calls"] or 0),
        "missed_calls": int(counts["missed_calls"] or 0),
        "completed_calls": int(counts["completed_calls"] or 0),
        "failed_or_busy_calls": int(counts["failed_or_busy_calls"] or 0),
        "no_answer_calls": int(counts["no_answer_calls"] or 0),
        "average_duration_seconds": int(counts["average_duration_seconds"] or 0),
        "recent_calls": [format_call_row(dict(row)) for row in recent],
        "missed_calls_list": [format_call_row(dict(row)) for row in missed],
    }


def _phone_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def search_patients(prod_db: Session, query: str, limit: int) -> list[dict[str, Any]]:
    """Patients (appointments) matching a name, phone number or email."""
    term = query.strip()

    if not term:
        return []

    rows = (
        prod_db.execute(
            text(
                f"""
            SELECT
                a.appointment_id, a.first_name, a.last_name,
                CONCAT_WS(' ', a.first_name, a.last_name) AS full_name,
                a.phone, a.email, a.appointment_type, a.category,
                a.appointment_datetime, a.date, a.time, a.clinic_id, a.location,
                a.calendar, c.name AS clinic_name, c.city AS clinic_city
            FROM appointment a
            LEFT JOIN clinics c ON c.id = a.clinic_id
            WHERE
                a.first_name ILIKE :text_query
                OR a.last_name ILIKE :text_query
                OR CONCAT_WS(' ', a.first_name, a.last_name) ILIKE :text_query
                OR a.email ILIKE :text_query
                OR {_DIGITS_ONLY.format(column="a.phone")} ILIKE :phone_query
            ORDER BY
                COALESCE(a.appointment_datetime, a.date) DESC NULLS LAST,
                a.created_at DESC NULLS LAST
            LIMIT :limit
            """
            ),
            {
                "text_query": f"%{term}%",
                "phone_query": f"%{_phone_digits(term) or term}%",
                "limit": limit,
            },
        )
        .mappings()
        .all()
    )

    return [
        {
            **dict(row),
            "appointment_datetime": _text_or_none(_utc(row["appointment_datetime"])),
            "date": _text_or_none(row["date"]),
            "time": _text_or_none(row["time"]),
        }
        for row in rows
    ]


def patient_call_history(prod_db: Session, phone: str, limit: int) -> list[dict[str, Any]]:
    """Every call to or from a phone number, newest first."""
    digits = _phone_digits(phone)

    if not digits:
        return []

    rows = (
        prod_db.execute(
            text(
                f"""
            {_CALL_SELECT}
            WHERE {_DIGITS_ONLY.format(column="cl.patient_number")} ILIKE :phone_query
            ORDER BY cl.created_at DESC
            LIMIT :limit
            """
            ),
            {"phone_query": f"%{digits}%", "limit": limit},
        )
        .mappings()
        .all()
    )

    return [format_call_row(dict(row)) for row in rows]


def calls_change_marker(prod_db: Session) -> tuple[Any, ...]:
    """A value that changes whenever a call is added or updated, for the live
    updates poll. Both the Command Center and the standalone Web Dialer write
    `call_logs`, so watching the table is how a change by either shows up."""
    row = prod_db.execute(
        text(
            """
            SELECT COUNT(*) AS n, MAX(id) AS last_id,
                   MAX(COALESCE(updated_at, created_at)) AS last_change
            FROM call_logs
            """
        )
    ).one()

    return tuple(row)


def find_outgoing_events(prod_db: Session, cc_call_ids: list[int]) -> list[dict[str, Any]]:
    """The standalone Web Dialer's record of each outgoing call it was asked to
    place for a Command Center call. The browser sends the Command Center call id
    as the `CcCallId` parameter; Twilio passes it on, with `From` (the caller's
    identity, set by Twilio from the token, so it cannot be forged), and the
    standalone service stores the whole request in `dialer_call_events`."""
    if not cc_call_ids:
        return []

    rows = (
        prod_db.execute(
            text(
                """
                SELECT e.call_log_id,
                       e.event_payload->>'CcCallId' AS cc_call_id,
                       e.event_payload->>'From' AS caller
                FROM dialer_call_events e
                WHERE e.event_type = 'outgoing_initiated'
                  AND e.call_log_id IS NOT NULL
                  AND e.event_payload->>'CcCallId' IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": [str(call_id) for call_id in cc_call_ids]},
        )
        .mappings()
        .all()
    )

    return [dict(row) for row in rows]


def get_call_log_states(prod_db: Session, call_log_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Twilio sid, status and duration of production call_logs rows, by id."""
    if not call_log_ids:
        return {}

    rows = (
        prod_db.execute(
            text(
                "SELECT id, call_sid, status, duration FROM call_logs WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": call_log_ids},
        )
        .mappings()
        .all()
    )

    return {int(row["id"]): dict(row) for row in rows}
