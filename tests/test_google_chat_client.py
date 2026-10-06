from __future__ import annotations

from datetime import datetime

import pytest

from app.integrations.google_chat_client import (
    PatientCheckedInCardData,
    _build_card,
    _show_admit_button,
)


def make_data(
    *,
    clinic_id: int | None = 1,
    appointment_datetime: datetime | None = datetime(2026, 10, 6, 10, 0),
    appointment_id: str = "APPT1",
    first_name: str | None = "Jane",
    phone: str | None = "+15551234567",
) -> PatientCheckedInCardData:
    return PatientCheckedInCardData(
        clinic_id=clinic_id,
        clinic_timezone="America/Chicago",
        last_name="Doe",
        first_name=first_name,
        appointment_id=appointment_id,
        gender=None,
        dob=None,
        exam_type="Ultrasound",
        reason=None,
        reason_other=None,
        physician_name=None,
        phone=phone,
        appointment_datetime=appointment_datetime,
        paid=True,
        checked_in_at=None,
        form_statuses=(),
        previous_report_url=None,
    )


def buttons(data: PatientCheckedInCardData) -> list[dict]:
    widgets = _build_card(data)["cardsV2"][0]["card"]["sections"][0]["widgets"]
    return widgets[-1]["buttonList"]["buttons"]


def url_of(button: dict) -> str:
    return button["onClick"]["openLink"]["url"]


# 2026-10-06 is a Tuesday, 2026-10-10 is a Saturday.
@pytest.mark.parametrize(
    ("clinic_id", "appointment_datetime", "expected"),
    [
        # Normal weekday hours: no admit button.
        (1, datetime(2026, 10, 6, 10, 0), False),
        (2, datetime(2026, 10, 6, 10, 0), False),
        # 8:30 AM boundary is inclusive, 8:31 AM is not.
        (1, datetime(2026, 10, 6, 8, 30), True),
        (1, datetime(2026, 10, 6, 8, 31), False),
        (1, datetime(2026, 10, 6, 7, 0), True),
        # Dallas cutoff is 5:30 PM.
        (1, datetime(2026, 10, 6, 17, 29), False),
        (1, datetime(2026, 10, 6, 17, 30), True),
        # Fairview cutoff is 6:00 PM, so 5:45 PM is still normal hours.
        (2, datetime(2026, 10, 6, 17, 45), False),
        (2, datetime(2026, 10, 6, 18, 0), True),
        # Weekends always show it.
        (1, datetime(2026, 10, 10, 11, 0), True),
        (2, datetime(2026, 10, 11, 11, 0), True),
        # A Friday evening is not a weekend, even though it is already
        # Saturday in UTC.
        (2, datetime(2026, 10, 9, 17, 0), False),
    ],
)
def test_show_admit_button_timing(
    clinic_id: int,
    appointment_datetime: datetime,
    expected: bool,
) -> None:
    data = make_data(clinic_id=clinic_id, appointment_datetime=appointment_datetime)

    assert _show_admit_button(data) is expected


def test_show_admit_button_false_without_appointment_time() -> None:
    assert _show_admit_button(make_data(appointment_datetime=None)) is False


def test_card_without_admit_has_ask_to_wait_and_call() -> None:
    result = buttons(make_data())

    assert [b["text"] for b in result] == ["Ask to wait", "📞 Call Patient"]


def test_card_with_admit_lists_all_three_buttons_in_order() -> None:
    result = buttons(make_data(appointment_datetime=datetime(2026, 10, 10, 11, 0)))

    assert [b["text"] for b in result] == [
        "Admit patient",
        "Ask to wait",
        "📞 Call Patient",
    ]


def test_admit_and_ask_to_wait_urls_carry_patient_details() -> None:
    result = buttons(
        make_data(
            appointment_datetime=datetime(2026, 10, 10, 11, 0),
            first_name="Jo Ann",
            phone="+15551234567",
        )
    )

    assert url_of(result[0]).endswith("/webhook/allow-entry?appointment_id=APPT1")
    assert url_of(result[1]).endswith(
        "/webhook/ask-to-wait?phone=%2B15551234567&name=Jo%20Ann&appointment_id=APPT1"
    )


def test_call_button_url_contains_the_appointment_id() -> None:
    call_button = buttons(make_data(appointment_id="a b/1"))[-1]

    assert url_of(call_button) == (
        "https://scanx-voice-calling-794794356928.us-central1.run.app/call-page"
        "?appointmentId=a%20b%2F1&token=ScanX50"
    )
    assert call_button["onClick"]["openLink"]["openAs"] == "FULL_SIZE"


def test_buttons_handle_missing_phone_and_name() -> None:
    result = buttons(make_data(first_name=None, phone=None))

    assert url_of(result[0]).endswith("/ask-to-wait?phone=&name=&appointment_id=APPT1")


def test_buttons_are_the_last_widget_after_the_details() -> None:
    widgets = _build_card(make_data())["cardsV2"][0]["card"]["sections"][0]["widgets"]

    assert "buttonList" in widgets[-1]
    assert all("textParagraph" in w for w in widgets[:-1])
    assert "Previous Report" in widgets[-2]["textParagraph"]["text"]
