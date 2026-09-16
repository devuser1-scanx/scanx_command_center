from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.integrations import jotform_links


def test_format_dob_converts_slashes_to_dashes() -> None:
    assert jotform_links.format_dob("04/12/1990") == "04-12-1990"


def test_format_dob_passes_through_unrecognized_shapes_unchanged() -> None:
    assert jotform_links.format_dob("1990-04-12") == "1990-04-12"
    assert jotform_links.format_dob("unknown") == "unknown"


def test_format_dob_handles_none_and_empty() -> None:
    assert jotform_links.format_dob(None) == ""
    assert jotform_links.format_dob("") == ""


def test_format_appointment_date_uses_utc_components() -> None:
    dt = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    assert jotform_links.format_appointment_date(dt) == "09-20-2026"


def test_format_appointment_date_converts_non_utc_timezone_to_utc() -> None:
    # 11:30pm UTC-4 on the 20th is 03:30 on the 21st in UTC - the date must
    # roll over to reflect the actual UTC calendar day.
    minus_four = timezone(timedelta(hours=-4))
    dt = datetime(2026, 9, 20, 23, 30, tzinfo=minus_four)
    assert jotform_links.format_appointment_date(dt) == "09-21-2026"


def test_format_appointment_date_handles_none() -> None:
    assert jotform_links.format_appointment_date(None) == ""


def test_build_pcp_declaration_form_url() -> None:
    url = jotform_links.build_pcp_declaration_form_url(
        first_name="Jane",
        last_name="Doe",
        dob="04/12/1990",
        appointment_datetime=datetime(2026, 9, 20, 14, 30, tzinfo=UTC),
        appointment_id="APT123",
    )

    assert url == (
        f"https://form.jotform.com/{jotform_links.PCP_DECLARATION_FORM_ID}"
        "?q3_textbox1=Jane%20Doe&dateOf=04-12-1990&appointmentDate=09-20-2026&appointmentId=APT123"
    )


def test_build_pcp_declaration_form_url_handles_missing_fields() -> None:
    url = jotform_links.build_pcp_declaration_form_url(
        first_name=None,
        last_name=None,
        dob=None,
        appointment_datetime=None,
        appointment_id="APT123",
    )

    assert url == (
        f"https://form.jotform.com/{jotform_links.PCP_DECLARATION_FORM_ID}"
        "?q3_textbox1=&dateOf=&appointmentDate=&appointmentId=APT123"
    )


def test_build_scrotal_consent_form_url_has_no_appointment_date_param() -> None:
    url = jotform_links.build_scrotal_consent_form_url(
        first_name="Jane",
        last_name="Doe",
        dob="04/12/1990",
        appointment_id="APT123",
    )

    assert url == (
        f"https://form.jotform.com/{jotform_links.SCROTAL_CONSENT_FORM_ID}"
        "?q3_textbox1=Jane%20Doe&dateOf=04-12-1990&appointmentId=APT123"
    )
    assert "appointmentDate" not in url


def test_build_transvag_consent_form_url_uses_q4_textbox2_field() -> None:
    url = jotform_links.build_transvag_consent_form_url(
        first_name="Jane",
        last_name="Doe",
        dob="04/12/1990",
        appointment_id="APT123",
    )

    assert url == (
        f"https://form.jotform.com/{jotform_links.TRANSVAG_CONSENT_FORM_ID}"
        "?q4_textbox2=Jane%20Doe&dateOf=04-12-1990&appointmentId=APT123"
    )
    assert "q3_textbox1" not in url
