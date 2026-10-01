from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

ACUITY_API_BASE = "https://acuityscheduling.com/api/v1"
ACUITY_REQUEST_TIMEOUT_SECONDS = 15.0


class AcuityApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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

    raise AcuityApiError(_extract_acuity_error(response))


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
