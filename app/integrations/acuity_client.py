from __future__ import annotations

import httpx

from app.core.config import settings

ACUITY_API_BASE = "https://acuityscheduling.com/api/v1"


class AcuityApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def get_reschedule_link(*, appointment_id: str) -> str:
    """Looks up the appointment on Acuity and returns its client-facing
    confirmation page URL, which Acuity also lets the client use to
    reschedule (or cancel) from.
    """
    if not (settings.acuity_user_id and settings.acuity_api_key):
        raise RuntimeError(
            "ACUITY_USER_ID and ACUITY_API_KEY must be configured to fetch reschedule links."
        )

    url = f"{ACUITY_API_BASE}/appointments/{appointment_id}"

    try:
        with httpx.Client(
            timeout=15.0,
            auth=(settings.acuity_user_id, settings.acuity_api_key),
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise AcuityApiError(f"Could not reach Acuity: {exc}") from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            error_message = payload.get("message", response.text)
        except ValueError:
            error_message = response.text

        raise AcuityApiError(error_message)

    payload = response.json()
    confirmation_page = payload.get("confirmationPage")

    if not confirmation_page:
        raise AcuityApiError("Acuity's response did not include a confirmationPage URL.")

    return confirmation_page
