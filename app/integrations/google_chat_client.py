from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote
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


def _show_admit_button(
    data: PatientCheckedInCardData,
) -> bool:
    """
    "Admit patient" is shown only for appointments outside normal hours:
    weekends, 8:30 AM or earlier, Dallas at/after 5:30 PM, and Fairview
    at/after 6:00 PM. Evaluated in the clinic's local time.
    """
    if data.appointment_datetime is None:
        return False

    timezone = ZoneInfo(data.clinic_timezone or "America/Chicago")
    value = data.appointment_datetime

    local = (
        value.replace(tzinfo=timezone)
        if value.tzinfo is None
        else value.astimezone(timezone)
    )

    total_minutes = local.hour * 60 + local.minute

    is_weekend = local.weekday() >= 5
    is_before_8_30 = total_minutes <= 8 * 60 + 30
    is_dallas_after_5_30 = data.clinic_id == 1 and total_minutes >= 17 * 60 + 30
    is_fairview_after_6 = data.clinic_id == 2 and total_minutes >= 18 * 60

    return is_weekend or is_before_8_30 or is_dallas_after_5_30 or is_fairview_after_6


def _button_list(
    data: PatientCheckedInCardData,
) -> dict:
    base_url = settings.n8n_webhook_base_url.rstrip("/")
    appointment_id = quote(data.appointment_id, safe="")

    admit_url = f"{base_url}/allow-entry?appointment_id={appointment_id}"
    ask_to_wait_url = (
        f"{base_url}/ask-to-wait"
        f"?phone={quote(data.phone or '', safe='')}"
        f"&name={quote(data.first_name or '', safe='')}"
        f"&appointment_id={appointment_id}"
    )

    buttons: list[dict] = []

    if _show_admit_button(data):
        buttons.append(
            {
                "text": "Admit patient",
                "onClick": {
                    "openLink": {
                        "url": admit_url,
                        "openAs": "FULL_SIZE",
                        "onClose": "NOTHING",
                    }
                },
            }
        )

    buttons.append(
        {
            "text": "Ask to wait",
            "onClick": {"openLink": {"url": ask_to_wait_url}},
        }
    )

    call_url = (
        "https://scanx-voice-calling-794794356928.us-central1.run.app/call-page"
        f"?appointmentId={appointment_id}&token=ScanX50"
    )
    buttons.append(
        {
            "text": "📞 Call Patient",
            "onClick": {
                "openLink": {
                    "url": call_url,
                    "openAs": "FULL_SIZE",
                    "onClose": "NOTHING",
                }
            },
        }
    )

    return {"buttonList": {"buttons": buttons}}


FIBROSCAN_EXAM_TYPE = "FibroScan / Liver Elastography"


def _fibroscan_report_file_name(
    data: PatientCheckedInCardData,
) -> str | None:
    """
    FibroScan / Liver Elastography exams get a "Report File Name" line:
    FIRSTNAME_LASTNAME_APPOINTMENTID, with the names upper-cased.
    """
    if not data.exam_type or FIBROSCAN_EXAM_TYPE not in data.exam_type:
        return None

    first_name = (data.first_name or "").upper()
    last_name = (data.last_name or "").upper()

    return f"{first_name}_{last_name}_{data.appointment_id}"


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

    report_file_name = _fibroscan_report_file_name(data)

    if report_file_name:
        widgets.append(
            {"textParagraph": {"text": (f"<b>Report File Name:</b> {escape(report_file_name)}")}}
        )

    widgets.append(_button_list(data))

    return {
        "cardsV2": [
            {
                "cardId": "appointment_card",
                "card": {
                    "header": {
                        "title": ("✅ Patient Checked In (Manual)"),
                        "subtitle": ("Find patient details below"),
                    },
                    "sections": [
                        # Card headers are plain text, so the highlight lives
                        # in its own section above the detail widgets.
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": (
                                            '<b><font color="#1a73e8">'
                                            "MANUAL CHECK-IN</font></b>"
                                        )
                                    }
                                }
                            ],
                        },
                        {
                            "widgets": widgets,
                        },
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
