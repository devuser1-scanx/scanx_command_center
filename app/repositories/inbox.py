from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

# Patient names for the Patient Inbox, from the production `appointment` table.
# Numbers are compared by their last 10 digits, so "+1 (469) 555-0100" and
# "4695550100" are the same patient (the same rule as the standalone Inbox).


def last_ten_digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def patient_names_by_last_ten(prod_db: Session, last_tens: list[str]) -> dict[str, str]:
    """{last 10 digits: "First Last"} for the numbers that belong to a patient.
    When a number has several appointments, the most recent one names it."""
    wanted = sorted({digits for digits in last_tens if digits})

    if not wanted:
        return {}

    rows = prod_db.execute(
        text(
            """
            SELECT DISTINCT ON (phone10) phone10, first_name, last_name
            FROM (
                SELECT
                    RIGHT(REGEXP_REPLACE(phone, '[^0-9]', '', 'g'), 10) AS phone10,
                    first_name, last_name, appointment_datetime, date, created_at
                FROM appointment
                WHERE phone IS NOT NULL
            ) a
            WHERE phone10 = ANY(:phones)
            ORDER BY phone10,
                     COALESCE(appointment_datetime, date) DESC NULLS LAST,
                     created_at DESC NULLS LAST
            """
        ),
        {"phones": wanted},
    ).all()

    names: dict[str, str] = {}

    for phone10, first_name, last_name in rows:
        name = f"{first_name or ''} {last_name or ''}".strip()

        if name:
            names[phone10] = name

    return names
