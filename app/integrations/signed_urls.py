from __future__ import annotations

import httpx

from app.core.config import settings


class SignedUrlError(Exception):
    pass


def get_signed_media_url(image_path: str) -> str:
    """A short-lived signed URL for a card image, from the signed-URL service
    (the same one the standalone Inbox uses). `image_path` always comes from the
    card catalog, never from a request."""
    try:
        response = httpx.get(
            settings.signed_url_service_url,
            params={"file": image_path},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise SignedUrlError("Failed to generate media URL.") from exc

    if response.status_code >= 400:
        raise SignedUrlError("Failed to generate media URL.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise SignedUrlError("Signed URL service returned unexpected response.") from exc

    url = payload.get("signedUrl") or payload.get("url") or payload.get("signed_url")

    if not isinstance(url, str) or not url:
        raise SignedUrlError("Signed URL service returned unexpected response.")

    return url
