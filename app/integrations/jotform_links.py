from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote, urlencode

# JotForm form template IDs. Fixed per form (not per-deployment config) -
# each corresponds to a specific, already-built JotForm form whose field
# names (q3_textbox1, dateOf, ...) this code prefills via query string.
PCP_DECLARATION_FORM_ID = "261322983766062"
SCROTAL_CONSENT_FORM_ID = "261443865777067"
TRANSVAG_CONSENT_FORM_ID = "261391142398056"


def format_dob(dob: str | None) -> str:
    """Passes a "MM/DD/YYYY"-shaped dob through as "MM-DD-YYYY" (JotForm's
    date field expects dashes). Any other shape is passed through unchanged
    rather than guessed at, since intake dob is free-text.

    Also reused by app/services/sms.py when recording a sent form link into
    production's form_tracking table, so that record matches exactly what
    was put in front of the patient.
    """
    if not dob:
        return ""

    parts = dob.split("/")

    if len(parts) == 3:
        return "-".join(parts)

    return dob


def format_appointment_date(appointment_datetime: datetime | None) -> str:
    """See format_dob's docstring - also reused for form_tracking."""
    if appointment_datetime is None:
        return ""

    return appointment_datetime.astimezone(UTC).strftime("%m-%d-%Y")


def build_pcp_declaration_form_url(
    *,
    first_name: str | None,
    last_name: str | None,
    dob: str | None,
    appointment_datetime: datetime | None,
    appointment_id: str,
) -> str:
    name = f"{first_name or ''} {last_name or ''}".strip()

    params = {
        "q3_textbox1": name,
        "dateOf": format_dob(dob),
        "appointmentDate": format_appointment_date(appointment_datetime),
        "appointmentId": appointment_id,
    }

    # quote_via=quote (not the default quote_plus) so spaces encode as %20,
    # matching the reference implementation's encodeURIComponent behavior.
    return (
        f"https://form.jotform.com/{PCP_DECLARATION_FORM_ID}?{urlencode(params, quote_via=quote)}"
    )


def build_scrotal_consent_form_url(
    *,
    first_name: str | None,
    last_name: str | None,
    dob: str | None,
    appointment_id: str,
) -> str:
    """No appointmentDate param, unlike the PCP form - the reference
    implementation for this form only prefills name/dob/appointmentId."""
    name = f"{first_name or ''} {last_name or ''}".strip()

    params = {
        "q3_textbox1": name,
        "dateOf": format_dob(dob),
        "appointmentId": appointment_id,
    }

    return (
        f"https://form.jotform.com/{SCROTAL_CONSENT_FORM_ID}?{urlencode(params, quote_via=quote)}"
    )


def build_transvag_consent_form_url(
    *,
    first_name: str | None,
    last_name: str | None,
    dob: str | None,
    appointment_id: str,
) -> str:
    """No appointmentDate param, same as the scrotal form. Note the name
    field is q4_textbox2 here, not q3_textbox1 - this form's field IDs
    differ from the other two per the reference implementation."""
    name = f"{first_name or ''} {last_name or ''}".strip()

    params = {
        "q4_textbox2": name,
        "dateOf": format_dob(dob),
        "appointmentId": appointment_id,
    }

    return (
        f"https://form.jotform.com/{TRANSVAG_CONSENT_FORM_ID}?{urlencode(params, quote_via=quote)}"
    )
