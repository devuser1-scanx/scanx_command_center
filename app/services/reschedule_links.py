from __future__ import annotations

from fastapi import HTTPException, status

from app.integrations.acuity_client import AcuityApiError, get_reschedule_link
from app.schemas.reschedule_links import RescheduleLinkResponse


def create_reschedule_link_for_appointment(
    *,
    appointment_id: str,
) -> RescheduleLinkResponse:
    """Asks Acuity for this appointment's confirmation page, which the
    patient can use to reschedule.

    `appointment_id` is passed straight through to Acuity - the production
    `appointment` table Command Center reads from is itself synced from
    Acuity, so its appointment_id is Acuity's own appointment ID.
    """
    try:
        url = get_reschedule_link(appointment_id=appointment_id)
    except AcuityApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not generate a reschedule link: {exc.message}",
        ) from exc

    return RescheduleLinkResponse(url=url)
