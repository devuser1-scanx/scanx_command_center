from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

ACUITY_API_BASE = "https://acuityscheduling.com/api/v1"
ACUITY_REQUEST_TIMEOUT_SECONDS = 15.0


class AcuityApiError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Acuity's own error code when it sent one (e.g. "not_available",
        # "required_email"); None for network or parsing failures.
        self.code = code


def _require_acuity_credentials() -> tuple[str, str]:
    """
    Return configured Acuity credentials.

    Acuity's v1 API uses HTTP Basic Auth where:
    - username = Acuity User ID
    - password = Acuity API Key
    """
    if not (settings.acuity_user_id and settings.acuity_api_key):
        raise RuntimeError("ACUITY_USER_ID and ACUITY_API_KEY must be configured.")

    return (
        settings.acuity_user_id,
        settings.acuity_api_key,
    )


def _extract_acuity_error(
    response: httpx.Response,
) -> str:
    """
    Extract the most useful error message available from an Acuity response.
    """
    try:
        payload = response.json()

        if isinstance(payload, dict):
            message = payload.get("message")

            if message:
                return str(message)

            error = payload.get("error")

            if error:
                return str(error)

    except ValueError:
        pass

    body = response.text.strip()

    if body:
        return body

    return f"Acuity request failed with HTTP {response.status_code}."


def _raise_for_acuity_error(
    response: httpx.Response,
) -> None:
    if response.status_code < 400:
        return

    code: str | None = None

    try:
        payload = response.json()
        raw_code = payload.get("error") if isinstance(payload, dict) else None
        code = str(raw_code) if raw_code else None
    except ValueError:
        pass

    raise AcuityApiError(_extract_acuity_error(response), code=code)


def get_reschedule_link(
    *,
    appointment_id: str,
) -> str:
    """
    Look up an appointment in Acuity and return its client-facing
    confirmation-page URL.

    Acuity lets the patient use that page to manage/reschedule/cancel the
    appointment where supported.
    """
    user_id, api_key = _require_acuity_credentials()

    url = f"{ACUITY_API_BASE}/appointments/{appointment_id}"

    try:
        with httpx.Client(
            timeout=ACUITY_REQUEST_TIMEOUT_SECONDS,
            auth=(user_id, api_key),
        ) as client:
            response = client.get(url)

    except httpx.HTTPError as exc:
        raise AcuityApiError(f"Could not reach Acuity: {exc}") from exc

    _raise_for_acuity_error(response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise AcuityApiError("Acuity returned an invalid JSON response.") from exc

    confirmation_page = payload.get("confirmationPage")

    if not confirmation_page:
        raise AcuityApiError("Acuity's response did not include a confirmationPage URL.")

    return str(confirmation_page)


def update_checked_in_label(
    *,
    appointment_id: str,
) -> None:
    """
    Apply Acuity's configured "Checked In" label to an appointment.

    This function performs only the Acuity API operation. It does not update
    the ScanX database and does not catch/suppress Acuity failures.

    The manual-check-in service will call this AFTER the production database
    check-in has successfully committed. A failure here therefore must not
    roll back the patient's local ScanX check-in.
    """
    user_id, api_key = _require_acuity_credentials()

    url = f"{ACUITY_API_BASE}/appointments/{appointment_id}"

    payload: dict[str, Any] = {"labels": [{"id": (settings.acuity_checked_in_label_id)}]}

    try:
        with httpx.Client(
            timeout=ACUITY_REQUEST_TIMEOUT_SECONDS,
            auth=(user_id, api_key),
        ) as client:
            response = client.put(
                url,
                json=payload,
            )

    except httpx.HTTPError as exc:
        raise AcuityApiError(f"Could not reach Acuity: {exc}") from exc

    _raise_for_acuity_error(response)


def _get_json_list(path: str) -> list[dict[str, Any]]:
    """GET a list endpoint from Acuity (server-side only; the API key never
    leaves the backend)."""
    user_id, api_key = _require_acuity_credentials()

    try:
        with httpx.Client(
            timeout=ACUITY_REQUEST_TIMEOUT_SECONDS,
            auth=(user_id, api_key),
        ) as client:
            response = client.get(f"{ACUITY_API_BASE}{path}")

    except httpx.HTTPError as exc:
        raise AcuityApiError(f"Could not reach Acuity: {exc}") from exc

    _raise_for_acuity_error(response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise AcuityApiError("Acuity returned an invalid JSON response.") from exc

    if not isinstance(payload, list):
        raise AcuityApiError("Acuity returned an unexpected response shape.")

    return [item for item in payload if isinstance(item, dict)]


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """One call to Acuity (server-side only; the API key never leaves the
    backend). Returns the decoded JSON."""
    user_id, api_key = _require_acuity_credentials()

    try:
        with httpx.Client(
            timeout=ACUITY_REQUEST_TIMEOUT_SECONDS,
            auth=(user_id, api_key),
        ) as client:
            response = client.request(
                method, f"{ACUITY_API_BASE}{path}", params=params, json=json_body
            )

    except httpx.HTTPError as exc:
        raise AcuityApiError(f"Could not reach Acuity: {exc}") from exc

    _raise_for_acuity_error(response)

    try:
        return response.json()
    except ValueError as exc:
        raise AcuityApiError("Acuity returned an invalid JSON response.") from exc


def list_availability_dates(
    *, appointment_type_id: int, calendar_id: int, month: str
) -> list[dict[str, Any]]:
    """GET /availability/dates: the days in `month` (YYYY-MM) that have open
    times for this exam at this clinic."""
    payload = _request(
        "GET",
        "/availability/dates",
        params={
            "month": month,
            "appointmentTypeID": str(appointment_type_id),
            "calendarID": str(calendar_id),
        },
    )

    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def list_availability_times(
    *, appointment_type_id: int, calendar_id: int, date: str
) -> list[dict[str, Any]]:
    """GET /availability/times: the open times on `date` (YYYY-MM-DD)."""
    payload = _request(
        "GET",
        "/availability/times",
        params={
            "date": date,
            "appointmentTypeID": str(appointment_type_id),
            "calendarID": str(calendar_id),
        },
    )

    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def create_appointment(body: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
    """POST /appointments. Books the appointment in Acuity itself, with no
    payment step (the API has none). `admin` skips Acuity's availability and
    required-field checks, so it is used only when the patient has no email."""
    payload = _request(
        "POST",
        "/appointments",
        params={"admin": "true"} if admin else None,
        json_body=body,
    )

    if not isinstance(payload, dict) or payload.get("id") is None:
        raise AcuityApiError("Acuity did not return the new appointment.")

    return payload


def list_calendars() -> list[dict[str, Any]]:
    """GET /calendars: the clinics (Acuity calendars)."""
    return _get_json_list("/calendars")


def list_forms() -> list[dict[str, Any]]:
    """GET /forms: the intake forms, each with its fields (ids, labels, dropdown
    options) and the appointment types it is attached to."""
    return _get_json_list("/forms")


def list_appointment_types() -> list[dict[str, Any]]:
    """GET /appointment-types: the exams, with the calendarIDs that offer each."""
    return _get_json_list("/appointment-types")
