from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.acuity_client import (
    AcuityApiError,
    update_checked_in_label,
)
from app.integrations.google_chat_client import (
    FormStatusForChat,
    GoogleChatApiError,
    PatientCheckedInCardData,
    send_patient_checked_in_card,
)
from app.integrations.pdf_proxy_client import (
    PdfProxyApiError,
    lookup_previous_report_link,
)
from app.integrations.twilio_client import TwilioApiError, send_content_message, send_sms
from app.models.auth import CCUser
from app.models.production import Appointment
from app.repositories.patients import (
    get_appointment_by_appointment_id,
    get_clinic,
    get_patient_intake,
    get_previous_checked_in_appointment,
    list_form_tracking,
)
from app.repositories.production_writes import (
    ManualCheckInWriteResult,
    manual_check_in_appointment,
)
from app.repositories.sms import (
    create_sms_transmission,
)
from app.schemas.patients import (
    ManualCheckInIntegrationStatuses,
    ManualCheckInResponse,
)
from app.services.audit import (
    record_user_activity,
)

logger = logging.getLogger(__name__)

RCS_PURPOSE = "checkin_payment_reminder"

RCS_LOG_BODY = "Twilio payment-reminder Content Template triggered after manual check-in."
CHECKIN_CONFIRMATION_PURPOSE = "manual_checkin_confirmation"

CHICAGO_TIMEZONE = ZoneInfo("America/Chicago")

def _format_checkin_time_for_patient_sms(

    checked_in_at: datetime,

) -> str:

    if checked_in_at.tzinfo is None:

        checked_in_at = checked_in_at.replace(

            tzinfo=UTC

        )

    local_time = checked_in_at.astimezone(

        CHICAGO_TIMEZONE

    )

    return (

        local_time.strftime("%I:%M %p %Z")

        .lstrip("0")

    )

def _as_utc_aware(

    value: datetime | None,

) -> datetime | None:

    if value is None:

        return None

    if value.tzinfo is None:

        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)

def _commit_manual_checkin(

    prod_db: Session,

    *,

    appointment_id: str,

    location: str | None,

) -> ManualCheckInWriteResult:

    try:

        result = manual_check_in_appointment(

            prod_db,

            appointment_id=appointment_id,

            location=location,

        )

        if result is None:

            prod_db.rollback()

            raise HTTPException(

                status_code=(status.HTTP_404_NOT_FOUND),

                detail="Appointment not found.",

            )

        if not result.already_checked_in:

            prod_db.commit()

        return result

    except HTTPException:

        raise

    except Exception as exc:

        prod_db.rollback()

        logger.exception(

            "Failed to persist manual check-in (appointment_id=%s)",

            appointment_id,

        )

        raise HTTPException(

            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),

            detail=("The patient could not be checked in."),

        ) from exc

def _record_audit_best_effort(

    db: Session,

    *,

    actor: CCUser,

    appointment: Appointment,

    checked_in_at: datetime | None,

    ip_address: str | None,

    user_agent: str | None,

) -> str:

    try:

        timestamp = _as_utc_aware(checked_in_at)

        record_user_activity(

            db,

            actor_user_id=actor.id,

            action=("appointment.manual_checkin"),

            resource_type="appointment",

            resource_id=(appointment.appointment_id),

            clinic_id=(appointment.clinic_id),

            details={

                "source": "command_center",

                "checkin_time": (timestamp.isoformat() if timestamp else None),

            },

            ip_address=ip_address,

            user_agent=user_agent,

        )

        db.commit()

        return "recorded"

    except Exception:

        db.rollback()

        logger.exception(

            "Manual check-in succeeded but audit logging failed (appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed"

def _update_acuity_best_effort(

    appointment_id: str,

) -> str:

    try:

        update_checked_in_label(appointment_id=appointment_id)

        return "updated"

    except (

        AcuityApiError,

        RuntimeError,

    ):

        logger.exception(

            "Manual check-in succeeded but Acuity label update failed (appointment_id=%s)",

            appointment_id,

        )

        return "failed"

    except Exception:

        logger.exception(

            "Unexpected Acuity failure after manual check-in (appointment_id=%s)",

            appointment_id,

        )

        return "failed"

def _lookup_previous_report_best_effort(

    prod_db: Session,

    *,

    appointment: Appointment,

) -> str | None:

    previous = get_previous_checked_in_appointment(

        prod_db,

        appointment=appointment,

    )

    if previous is None:

        return None

    try:

        return lookup_previous_report_link(

            first_name=previous.first_name,

            last_name=previous.last_name,

            appointment_id=(previous.appointment_id),

        )

    except PdfProxyApiError:

        logger.exception(

            "Previous-report lookup failed (appointment_id=%s)",

            previous.appointment_id,

        )

        return None

    except Exception:

        logger.exception(

            "Unexpected previous-report lookup failure (appointment_id=%s)",

            previous.appointment_id,

        )

        return None

def _send_google_chat_best_effort(

    prod_db: Session,

    *,

    appointment: Appointment,

    clinic_timezone: str | None,

    checked_in_at: datetime | None,

) -> str:

    try:

        intake = get_patient_intake(

            prod_db,

            appointment.appointment_id,

        )

        form_rows = list_form_tracking(

            prod_db,

            appointment.appointment_id,

        )

        previous_report_url = _lookup_previous_report_best_effort(

            prod_db,

            appointment=appointment,

        )

        card_data = PatientCheckedInCardData(

            clinic_id=(appointment.clinic_id),

            clinic_timezone=(clinic_timezone),

            last_name=(appointment.last_name),

            first_name=(appointment.first_name),

            appointment_id=(appointment.appointment_id),

            gender=(intake.gender if intake else None),

            dob=(intake.dob if intake else None),

            exam_type=(appointment.appointment_type or appointment.category or "Exam"),

            reason=(intake.reason if intake else None),

            reason_other=(intake.reason_other if intake else None),

            physician_name=(intake.physician_name if intake else None),

            phone=appointment.phone,

            appointment_datetime=(appointment.appointment_datetime),

            paid=appointment.paid,

            checked_in_at=(checked_in_at),

            form_statuses=tuple(

                FormStatusForChat(

                    form_type=row.form_type,

                    status=row.status,

                )

                for row in form_rows

            ),

            previous_report_url=(previous_report_url),

        )

        send_patient_checked_in_card(card_data)

        return "sent"

    except (

        GoogleChatApiError,

        RuntimeError,

    ):

        logger.exception(

            "Manual check-in succeeded but Google Chat notification failed (appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed"

    except Exception:

        logger.exception(

            "Unexpected Google Chat failure after manual check-in (appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed"

def _send_checkin_confirmation_sms_best_effort(
    db: Session,
    *,
    appointment: Appointment,
    actor: CCUser,
    checked_in_at: datetime | None,
) -> str:
    """
    Send the simple patient check-in confirmation SMS.

    This runs only after the authoritative DB check-in has committed.
    Failure here never rolls back the patient's check-in.
    """
    if not appointment.phone:
        return "skipped_no_phone"

    if checked_in_at is None:
        logger.error(
            "Manual check-in confirmation SMS skipped because checked_in_at "
            "is missing (appointment_id=%s)",
            appointment.appointment_id,
        )
        return "failed_no_checkin_time"

    checked_in_time = _format_checkin_time_for_patient_sms(
        checked_in_at
    )

    body = (
        "You're checked in. We have informed sonographers; "
        "please wait and someone will attend you shortly.\n"
        f"Checked-in time: {checked_in_time}"
    )

    try:
        result = send_sms(
            to=appointment.phone,
            body=body,
        )

        try:
            create_sms_transmission(
                db,
                appointment_id=appointment.appointment_id,
                purpose=CHECKIN_CONFIRMATION_PURPOSE,
                destination_number=appointment.phone,
                body=body,
                status="submitted",
                twilio_message_sid=result.message_sid,
                error_message=None,
                sent_by_user_id=actor.id,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Check-in confirmation SMS was sent but transmission logging "
                "failed (appointment_id=%s)",
                appointment.appointment_id,
            )

        return "submitted"

    except RuntimeError as exc:
        try:
            create_sms_transmission(
                db,
                appointment_id=appointment.appointment_id,
                purpose=CHECKIN_CONFIRMATION_PURPOSE,
                destination_number=appointment.phone,
                body=body,
                status="failed_to_submit",
                twilio_message_sid=None,
                error_message=str(exc),
                sent_by_user_id=actor.id,
            )
            db.commit()
        except Exception:
            db.rollback()

        logger.exception(
            "Manual check-in succeeded but confirmation SMS configuration "
            "is incomplete (appointment_id=%s)",
            appointment.appointment_id,
        )
        return "failed"

    except TwilioApiError as exc:
        try:
            create_sms_transmission(
                db,
                appointment_id=appointment.appointment_id,
                purpose=CHECKIN_CONFIRMATION_PURPOSE,
                destination_number=appointment.phone,
                body=body,
                status="failed_to_submit",
                twilio_message_sid=None,
                error_message=exc.message,
                sent_by_user_id=actor.id,
            )
            db.commit()
        except Exception:
            db.rollback()

        logger.exception(
            "Manual check-in succeeded but Twilio rejected the confirmation "
            "SMS (appointment_id=%s)",
            appointment.appointment_id,
        )
        return "failed"

    except Exception:
        db.rollback()
        logger.exception(
            "Unexpected confirmation SMS failure after manual check-in "
            "(appointment_id=%s)",
            appointment.appointment_id,
        )
        return "failed"


def _record_rcs_transmission_best_effort(

    db: Session,

    *,

    appointment: Appointment,

    actor: CCUser,

    status_value: str,

    message_sid: str | None,

    error_message: str | None,

) -> None:

    try:

        create_sms_transmission(

            db,

            appointment_id=(appointment.appointment_id),

            purpose=RCS_PURPOSE,

            destination_number=(appointment.phone or ""),

            body=RCS_LOG_BODY,

            status=status_value,

            twilio_message_sid=(message_sid),

            error_message=(error_message),

            sent_by_user_id=actor.id,

        )

        db.commit()

    except Exception:

        db.rollback()

        logger.exception(

            "Failed to record manual-check-in RCS transmission (appointment_id=%s)",

            appointment.appointment_id,

        )

def _send_payment_reminder_rcs_best_effort(

    db: Session,

    *,

    appointment: Appointment,

    actor: CCUser,

) -> str:

    """

    Preserve the existing check-in service behavior.

    Paid:

        no RCS

    No payment link:

        no RCS

    Otherwise:

        send TWILIO_PAYMENT_TEMPLATE_SID

        Content variable 1 = first name

        Content variable 2 = payment link

    """

    if appointment.paid is True:

        return "skipped_paid"

    if not appointment.payment_link:

        return "skipped_no_payment_link"

    if not appointment.phone:

        return "skipped_no_phone"

    content_sid = settings.twilio_payment_template_sid

    if not content_sid:

        logger.error(

            "TWILIO_PAYMENT_TEMPLATE_SID is not configured; RCS not sent (appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed_not_configured"

    try:

        result = send_content_message(

            to=appointment.phone,

            content_sid=content_sid,

            content_variables={

                "1": appointment.date.strftime("%m/%d/%Y") if appointment.date else "",

                "2": appointment.time or "",

                "3": appointment.payment_link,

            },

        )

        _record_rcs_transmission_best_effort(

            db,

            appointment=appointment,

            actor=actor,

            status_value="submitted",

            message_sid=(result.message_sid),

            error_message=None,

        )

        return "submitted"

    except RuntimeError as exc:

        _record_rcs_transmission_best_effort(

            db,

            appointment=appointment,

            actor=actor,

            status_value=("failed_to_submit"),

            message_sid=None,

            error_message=str(exc),

        )

        logger.exception(

            "Manual check-in succeeded but "

            "payment-reminder RCS configuration "

            "is incomplete "

            "(appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed"

    except TwilioApiError as exc:

        _record_rcs_transmission_best_effort(

            db,

            appointment=appointment,

            actor=actor,

            status_value=("failed_to_submit"),

            message_sid=None,

            error_message=exc.message,

        )

        logger.exception(

            "Manual check-in succeeded but "

            "Twilio rejected payment-reminder RCS "

            "(appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed"

    except Exception:

        logger.exception(

            "Unexpected Twilio failure after manual check-in (appointment_id=%s)",

            appointment.appointment_id,

        )

        return "failed"

def manual_check_in_patient(

    db: Session,

    prod_db: Session,

    *,

    appointment_id: str,

    actor: CCUser,

    ip_address: str | None,

    user_agent: str | None,

) -> ManualCheckInResponse:

    """

    Perform a Command Center Manual Check-in.

    The local production DB check-in is authoritative.

    Acuity, audit, Google Chat and Twilio happen only after that

    transaction succeeds. Failures in those systems do not undo the

    patient's local check-in.

    """

    appointment_before = get_appointment_by_appointment_id(

        prod_db,

        appointment_id,

    )

    if appointment_before is None:

        raise HTTPException(

            status_code=(status.HTTP_404_NOT_FOUND),

            detail="Appointment not found.",

        )

    clinic = (

        get_clinic(

            prod_db,

            appointment_before.clinic_id,

        )

        if appointment_before.clinic_id

        else None

    )

    location = clinic.name if clinic else None

    write_result = _commit_manual_checkin(

        prod_db,

        appointment_id=appointment_id,

        location=location,

    )

    if write_result.already_checked_in:

        return ManualCheckInResponse(

            success=True,

            message=("Patient is already checked in."),

            appointment_id=appointment_id,

            checked_in=True,

            checked_in_at=_as_utc_aware(write_result.checked_in_at),

            already_checked_in=True,

            integrations=(

                ManualCheckInIntegrationStatuses(

                    audit=("skipped_already_checked_in"),

                    acuity=("skipped_already_checked_in"),

                    google_chat=("skipped_already_checked_in"),

                    payment_reminder_rcs=("skipped_already_checked_in"),

                )

            ),

        )

    appointment = write_result.appointment

    checked_in_at = write_result.checked_in_at

    audit_status = _record_audit_best_effort(

        db,

        actor=actor,

        appointment=appointment,

        checked_in_at=checked_in_at,

        ip_address=ip_address,

        user_agent=user_agent,

    )

    acuity_status = _update_acuity_best_effort(appointment.appointment_id)

    google_chat_status = _send_google_chat_best_effort(

        prod_db,

        appointment=appointment,

        clinic_timezone=(clinic.timezone if clinic else None),

        checked_in_at=(checked_in_at),

    )

    _send_checkin_confirmation_sms_best_effort(

        db,

        appointment=appointment,

        actor=actor,

        checked_in_at=checked_in_at,

    )


    rcs_status = _send_payment_reminder_rcs_best_effort(

        db,

        appointment=appointment,

        actor=actor,

    )

    return ManualCheckInResponse(

        success=True,

        message=("Patient checked in successfully."),

        appointment_id=(appointment.appointment_id),

        checked_in=True,

        checked_in_at=_as_utc_aware(checked_in_at),

        already_checked_in=False,

        integrations=(

            ManualCheckInIntegrationStatuses(

                audit=audit_status,

                acuity=acuity_status,

                google_chat=(google_chat_status),

                payment_reminder_rcs=(rcs_status),

            )

        ),

    )
