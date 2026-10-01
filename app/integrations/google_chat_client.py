from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class FormStatusForChat:
    form_type: str
    status: str


@dataclass(frozen=True)
class PatientCheckedInCardData:
    clinic_id: int | None
    clinic_timezone: str | None

    last_name: str | None
    first_name: str | None
    appointment_id: str

    gender: str | None
    dob: str | None

    exam_type: str | None

    reason: str | None
    reason_other: str | None

    physician_name: str | None

    phone: str | None

    appointment_datetime: datetime | None

    paid: bool | None

    checked_in_at: datetime | None

    form_statuses: tuple[
        FormStatusForChat,
        ...,
    ]

    previous_report_url: str | None


class GoogleChatApiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _webhook_for_clinic(
    clinic_id: int | None,
) -> str:
    if clinic_id == 1:
        if not settings.gchat_webhook_dallas:
            raise RuntimeError(
                "Google Chat webhook is not configured for Dallas."
            )

        return settings.gchat_webhook_dallas

    if clinic_id == 2:
        if not settings.gchat_webhook_fairview:
            raise RuntimeError(
                "Google Chat webhook is not configured for Fairview."
            )

        return settings.gchat_webhook_fairview

    raise RuntimeError(
        f"No Google Chat routing is configured for clinic_id={clinic_id}."
    )


def _display(
    value: object | None,
) -> str:
    if value is None:
        return "N/A"

    text = str(value).strip()

    if not text:
        return "N/A"

    return escape(text)


def _format_local_time(
    value: datetime | None,
    timezone_name: str | None,
    *,
    naive_is_utc: bool,
) -> str:
    if value is None:
        return "N/A"

    timezone = ZoneInfo(timezone_name or "America/Chicago")

    if value.tzinfo is None:
        source = (
            value.replace(tzinfo=UTC)
            if naive_is_utc
            else value.replace(tzinfo=timezone)
        )
    else:
        source = value

    local = source.astimezone(timezone)

    return local.strftime("%I:%M %p").lstrip("0")


def _form_status_html(
    statuses: tuple[
        FormStatusForChat,
        ...,
    ],
) -> str:
    if not statuses:
        return "The patient does not require to fill any form"

    parts: list[str] = []

    for item in sorted(
        statuses,
        key=lambda row: row.form_type.lower(),
    ):
        entry = f"{_display(item.form_type)}: {_display(item.status)}"

        if item.status.strip().lower() == "pending":
            entry = f'<font color="#d93025">{entry}</font>'

        parts.append(entry)

    return "; ".join(parts)


def _build_card(
    data: PatientCheckedInCardData,
) -> dict:
    paid_display = (
    "true"
    if data.paid is True
    else '<font color="#d93025">false</font>'
)

    if data.previous_report_url:
        previous_report_display = (
            f'<a href="{escape(data.previous_report_url, quote=True)}">Download</a>'
        )
    else:
        previous_report_display = "N/A"

    # BUSINESS REQUIREMENT:
    # Do not reorder these widgets.
    widgets = [
        {"textParagraph": {"text": (f"<b>Last Name:</b> {_display(data.last_name)}")}},
        {"textParagraph": {"text": (f"<b>First Name:</b> {_display(data.first_name)}")}},
        {
            "textParagraph": {
                "text": (f"<b>Patient / Appointment ID:</b> {_display(data.appointment_id)}")
            }
        },
        {"textParagraph": {"text": (f"<b>Gender:</b> {_display(data.gender)}")}},
        {"textParagraph": {"text": (f"<b>DOB (MM/DD/YYYY):</b> {_display(data.dob)}")}},
        {"textParagraph": {"text": (f"<b>Exam Type:</b> {_display(data.exam_type)}")}},
        {"textParagraph": {"text": (f"<b>Reason for Appointment:</b> {_display(data.reason)}")}},
        {"textParagraph": {"text": (f"<b>Reason (Other):</b> {_display(data.reason_other)}")}},
        {
            "textParagraph": {
                "text": (f"<b>Primary Care Provider:</b> {_display(data.physician_name)}")
            }
        },
        {"textParagraph": {"text": (f"<b>Phone:</b> {_display(data.phone)}")}},
        {
            "textParagraph": {
                "text": (
                    "<b>Appointment Time:</b> "
                    + _format_local_time(
                        data.appointment_datetime,
                        data.clinic_timezone,
                        naive_is_utc=False,
                    )
                )
            }
        },
        {"textParagraph": {"text": (f"<b>Paid:</b> {paid_display}")}},
        {
            "textParagraph": {
                "text": (
                    "<b>Checked-in Time:</b> "
                    + _format_local_time(
                        data.checked_in_at,
                        data.clinic_timezone,
                        naive_is_utc=True,
                    )
                )
            }
        },
        {
            "textParagraph": {
                "text": (f"<b>Form Status:</b> {_form_status_html(data.form_statuses)}")
            }
        },
        {"textParagraph": {"text": (f"<b>Previous Report:</b> {previous_report_display}")}},
    ]

    return {
        "cardsV2": [
            {
                "cardId": "appointment_card",
                "card": {
                    "header": {
                        "title": ("✅ Patient Checked In"),
                        "subtitle": ("Find patient details below"),
                    },
                    "sections": [
                        {
                            "widgets": widgets,
                        }
                    ],
                },
            }
        ]
    }


def send_patient_checked_in_card(
    data: PatientCheckedInCardData,
) -> None:
    webhook_url = _webhook_for_clinic(data.clinic_id)

    card = _build_card(data)

    try:
        with httpx.Client(
            timeout=15.0,
        ) as client:
            response = client.post(
                webhook_url,
                json=card,
            )

    except httpx.HTTPError as exc:
        raise GoogleChatApiError(f"Could not reach Google Chat: {exc}") from exc

    if response.status_code >= 400:
        raise GoogleChatApiError(
            "Google Chat rejected the check-in card "
            f"with HTTP {response.status_code}: "
            f"{response.text}"
        )
