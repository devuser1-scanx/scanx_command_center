from __future__ import annotations

import httpx

from app.core.config import settings


class PdfProxyApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def create_short_url(*, gcs_blob_name: str) -> str:
    """Asks the already-deployed scanx-pdf-proxy service to mint a short,
    self-expiring download link for a report blob already sitting in the
    scanx-reports bucket.

    That service owns its own link storage (Firestore), expiry (7 days), and
    patient-facing "request a new link" self-service page - Command Center
    doesn't need to duplicate any of that, only ask it for a link.
    """
    url = f"{settings.pdf_proxy_base_url.rstrip('/')}/api/create-short-url"

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                url,
                json={
                    "fileName": gcs_blob_name,
                },
            )
    except httpx.HTTPError as exc:
        raise PdfProxyApiError(
            f"Could not reach the report link service: {exc}"
        ) from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            error_message = payload.get(
                "error",
                response.text,
            )
        except ValueError:
            error_message = response.text

        raise PdfProxyApiError(
            error_message
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PdfProxyApiError(
            "The report link service returned an invalid JSON response."
        ) from exc

    short_url = payload.get(
        "shortUrl"
    )

    if not short_url:
        raise PdfProxyApiError(
            "The report link service did not return a shortUrl."
        )

    return str(short_url)


def lookup_previous_report_link(
    *,
    first_name: str | None,
    last_name: str | None,
    appointment_id: str,
) -> str | None:
    """
    Ask the existing ScanX PDF proxy for the previous appointment's
    report short URL.

    Returns None when the service reports that no previous report was found.
    """
    url = (
        f"{settings.pdf_proxy_base_url.rstrip('/')}"
        "/api/lookup-previous-report"
    )

    try:
        with httpx.Client(
            timeout=15.0,
        ) as client:
            response = client.post(
                url,
                json={
                    "firstName": first_name or "",
                    "lastName": last_name or "",
                    "appointmentId": appointment_id,
                },
            )

    except httpx.HTTPError as exc:
        raise PdfProxyApiError(
            f"Could not reach the report link service: {exc}"
        ) from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            error_message = (
                payload.get("error")
                or payload.get("message")
                or response.text
            )
        except ValueError:
            error_message = response.text

        raise PdfProxyApiError(
            str(error_message)
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PdfProxyApiError(
            "The report link service returned an invalid JSON response."
        ) from exc

    if not payload.get("found"):
        return None

    short_url = payload.get(
        "shortUrl"
    )

    return (
        str(short_url)
        if short_url
        else None
    )