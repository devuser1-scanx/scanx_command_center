from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.services.dashboard import derive_status_and_tone, is_late, is_paid


@dataclass
class FakeAppointment:
    canceled: bool | None = None
    status_label: str | None = None
    token_used: bool | None = None
    confirmed: bool | None = None
    prep_ack: bool | None = None
    paid: bool | None = None
    appointment_datetime: datetime | None = None


# --- derive_status_and_tone ---------------------------------------------


def test_canceled_flag_takes_top_precedence_over_everything_else() -> None:
    appointment = FakeAppointment(canceled=True, token_used=True, confirmed=True)

    assert derive_status_and_tone(appointment) == ("Cancelled", "purple")


def test_status_label_cancelled_also_yields_cancelled() -> None:
    appointment = FakeAppointment(status_label="Cancelled")

    assert derive_status_and_tone(appointment) == ("Cancelled", "purple")


def test_no_show_status_label() -> None:
    appointment = FakeAppointment(status_label="No Show")

    assert derive_status_and_tone(appointment) == ("No Show", "red")


def test_completed_status_label_variants() -> None:
    assert derive_status_and_tone(FakeAppointment(status_label="Completed")) == (
        "Completed",
        "pink",
    )
    assert derive_status_and_tone(FakeAppointment(status_label="Complete")) == (
        "Completed",
        "pink",
    )


def test_checked_in_via_token_used_flag() -> None:
    appointment = FakeAppointment(token_used=True)

    assert derive_status_and_tone(appointment) == ("Checked In", "green")


def test_checked_in_via_status_label_fallback() -> None:
    appointment = FakeAppointment(status_label="Checked In")

    assert derive_status_and_tone(appointment) == ("Checked In", "green")


def test_confirmed_status_label_with_prep_ack_true_is_confirmed() -> None:
    appointment = FakeAppointment(status_label="Confirmed", prep_ack=True)

    assert derive_status_and_tone(appointment) == ("Confirmed", "yellow")


def test_confirmed_status_label_without_prep_ack_means_paid_not_confirmed() -> None:
    """status_label == "Confirmed" on production data actually signals
    payment, not real confirmation - it only counts as real confirmation
    when prep_ack is also true (see derive_status_and_tone's docstring)."""
    appointment = FakeAppointment(status_label="Confirmed", prep_ack=False)

    assert derive_status_and_tone(appointment) == ("Paid", "teal")

    appointment_no_prep_ack_at_all = FakeAppointment(status_label="Confirmed", prep_ack=None)

    assert derive_status_and_tone(appointment_no_prep_ack_at_all) == ("Paid", "teal")


def test_confirmed_boolean_column_independent_of_status_label() -> None:
    """The raw `confirmed` column still means real confirmation on its own,
    unaffected by the status_label="Confirmed" quirk."""
    appointment = FakeAppointment(confirmed=True, status_label=None)

    assert derive_status_and_tone(appointment) == ("Confirmed", "yellow")


def test_scheduled_is_the_fallback() -> None:
    appointment = FakeAppointment()

    assert derive_status_and_tone(appointment) == ("Scheduled", "blue")


def test_checked_in_takes_precedence_over_confirmed_status_label() -> None:
    appointment = FakeAppointment(status_label="Confirmed", prep_ack=True, token_used=True)

    assert derive_status_and_tone(appointment) == ("Checked In", "green")


def test_completed_takes_precedence_over_checked_in() -> None:
    appointment = FakeAppointment(status_label="Completed", token_used=True)

    assert derive_status_and_tone(appointment) == ("Completed", "pink")


# --- is_paid --------------------------------------------------------------


def test_is_paid_true_via_raw_paid_column() -> None:
    appointment = FakeAppointment(paid=True, status_label=None)

    assert is_paid(appointment) is True


def test_is_paid_true_via_status_label_confirmed_fallback() -> None:
    """Even once status_label has moved on past "Confirmed" (e.g. after
    check-in), the raw paid column should still be checked first - but if
    it's unset, status_label == "Confirmed" is still evidence of payment."""
    appointment = FakeAppointment(paid=None, status_label="Confirmed")

    assert is_paid(appointment) is True


def test_is_paid_false_when_neither_signal_present() -> None:
    appointment = FakeAppointment(paid=False, status_label="Checked In")

    assert is_paid(appointment) is False

    appointment_all_none = FakeAppointment()

    assert is_paid(appointment_all_none) is False


# --- is_late ---------------------------------------------------------------


def test_is_late_true_for_past_unresolved_appointment() -> None:
    appointment = FakeAppointment(
        appointment_datetime=datetime.now(UTC) - timedelta(hours=1),
    )

    assert is_late(appointment, now_utc=datetime.now(UTC)) is True


def test_is_late_false_when_canceled() -> None:
    appointment = FakeAppointment(
        canceled=True,
        appointment_datetime=datetime.now(UTC) - timedelta(hours=1),
    )

    assert is_late(appointment, now_utc=datetime.now(UTC)) is False


def test_is_late_false_when_checked_in() -> None:
    appointment = FakeAppointment(
        token_used=True,
        appointment_datetime=datetime.now(UTC) - timedelta(hours=1),
    )

    assert is_late(appointment, now_utc=datetime.now(UTC)) is False


def test_is_late_false_when_completed_or_no_show() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)

    assert (
        is_late(
            FakeAppointment(status_label="Completed", appointment_datetime=past),
            now_utc=datetime.now(UTC),
        )
        is False
    )
    assert (
        is_late(
            FakeAppointment(status_label="No Show", appointment_datetime=past),
            now_utc=datetime.now(UTC),
        )
        is False
    )


def test_is_late_false_for_future_appointment() -> None:
    appointment = FakeAppointment(
        appointment_datetime=datetime.now(UTC) + timedelta(hours=1),
    )

    assert is_late(appointment, now_utc=datetime.now(UTC)) is False


def test_is_late_false_when_no_appointment_datetime() -> None:
    appointment = FakeAppointment(appointment_datetime=None)

    assert is_late(appointment, now_utc=datetime.now(UTC)) is False
