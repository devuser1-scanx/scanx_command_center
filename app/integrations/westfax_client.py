from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

from app.core.config import settings


class WestFaxApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class FaxAttachment:
    filename: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class FaxJobStatus:
    job_id: str
    query_success: bool
    job_state: str | None
    code: str | None


def _parse_result_of_string(response_text: str) -> str:
    """Parses WestFax's `ApiResultOfString` XML envelope (Success/ErrorString/Result)."""
    root = ElementTree.fromstring(response_text)

    success = (root.findtext("Success") or "").strip().lower() == "true"

    if not success:
        error_string = (root.findtext("ErrorString") or "WestFax request failed.").strip()
        raise WestFaxApiError(error_string)

    result = root.findtext("Result")

    if not result:
        raise WestFaxApiError("WestFax response did not include a Result value.")

    return result


class WestFaxClient:
    """Thin wrapper around WestFax's REST fax API.

    Uses the "stateless" call style documented by WestFax: Username,
    Password and ProductId are sent on every call, so there is no session
    token to manage or expire.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        product_id: str,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._product_id = product_id
        self._timeout = timeout

    def send_fax(
        self,
        *,
        numbers: list[str],
        files: list[FaxAttachment],
        header: str,
        billing_code: str,
        job_name: str,
    ) -> str:
        """Sends one fax job. All files are combined by WestFax into a
        single document and sent to every number in `numbers`.

        Returns the WestFax job id (a GUID string) on success.
        """
        if not numbers:
            raise ValueError("At least one destination number is required.")

        if not files:
            raise ValueError("At least one file is required.")

        data: dict[str, str] = {
            "Username": self._username,
            "Password": self._password,
            "ProductId": self._product_id,
            "JobName": job_name,
            "BillingCode": billing_code,
            "Header": header,
        }

        for index, number in enumerate(numbers, start=1):
            data[f"Numbers{index}"] = number

        upload_files = [
            (
                f"Files{index}",
                (attachment.filename, attachment.content, attachment.content_type),
            )
            for index, attachment in enumerate(files, start=1)
        ]

        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/SendFax/xml",
                data=data,
                files=upload_files,
            )

        response.raise_for_status()

        return _parse_result_of_string(response.text)

    def get_fax_status(self, job_ids: list[str]) -> list[FaxJobStatus]:
        """Queries the status of previously submitted fax jobs.

        Not currently used by any route - kept for a future status-refresh
        feature. The response parsing here has not been verified against a
        live WestFax account and should be confirmed before relying on it.
        """
        if not job_ids:
            return []

        data: dict[str, str] = {
            "Username": self._username,
            "Password": self._password,
            "ProductId": self._product_id,
        }

        for index, job_id in enumerate(job_ids, start=1):
            data[f"Ids{index}"] = job_id

        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/GetFaxStatus/xml",
                data=data,
            )

        response.raise_for_status()

        root = ElementTree.fromstring(response.text)

        success = (root.findtext("Success") or "").strip().lower() == "true"

        if not success:
            error_string = (root.findtext("ErrorString") or "WestFax request failed.").strip()
            raise WestFaxApiError(error_string)

        statuses: list[FaxJobStatus] = []

        for container in root.iter("JobStatusContainer"):
            statuses.append(
                FaxJobStatus(
                    job_id=container.findtext("JobId") or "",
                    query_success=(
                        (container.findtext("QuerySuccess") or "").strip().lower() == "true"
                    ),
                    job_state=container.findtext("JobState"),
                    code=container.findtext("Code"),
                )
            )

        return statuses


@dataclass(frozen=True)
class ProductInfo:
    id: str
    name: str


# --- One-time setup utility, safe to delete -----------------------------
# Only needed to discover WESTFAX_PRODUCT_ID before it's configured -
# SendFax/GetFaxStatus use the ID directly and never call this. Not
# referenced by WestFaxClient, get_westfax_client(), or any route.
# Delete this function and app/scripts/westfax_list_products.py together
# once you no longer need to look up a product ID.
def get_product_list(
    *,
    base_url: str,
    username: str,
    password: str,
    product_type: str = "Fax",
    timeout: float = 30.0,
) -> list[ProductInfo]:
    data = {
        "Username": username,
        "Password": password,
        "ProductType": product_type,
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url.rstrip('/')}/GetProductList/xml", data=data)

    response.raise_for_status()

    root = ElementTree.fromstring(response.text)

    success = (root.findtext("Success") or "").strip().lower() == "true"

    if not success:
        error_string = (root.findtext("ErrorString") or "WestFax request failed.").strip()
        raise WestFaxApiError(error_string)

    return [
        ProductInfo(
            id=container.findtext("Id") or "",
            name=container.findtext("Name") or "",
        )
        for container in root.iter("ProductContainer")
    ]


# --------------------------------------------------------------------------


def get_westfax_client() -> WestFaxClient:
    is_configured = (
        settings.westfax_username and settings.westfax_password and settings.westfax_product_id
    )

    if not is_configured:
        raise RuntimeError(
            "WESTFAX_USERNAME, WESTFAX_PASSWORD and WESTFAX_PRODUCT_ID must be configured "
            "to send a fax."
        )

    return WestFaxClient(
        base_url=settings.westfax_base_url,
        username=settings.westfax_username,
        password=settings.westfax_password,
        product_id=settings.westfax_product_id,
    )
