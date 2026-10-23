from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Fax Portal (cc_fp_) tables. Source of truth: docs/schema.sql (draft v3.2).
#
# Self-contained: the only foreign keys to existing tables are user columns
# -> cc_users(id). Pointers to other existing data (appointment
# .appointment_id, call_logs.id) are plain columns with no FK.


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _user_fk(*, nullable: bool = True) -> Mapped[int | None]:
    return mapped_column(
        BigInteger,
        ForeignKey("cc_users.id", ondelete="SET NULL"),
        nullable=nullable,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ---------------------------------------------------------------------
# 1. Lookups
# ---------------------------------------------------------------------


class CCFpStatus(Base):
    __tablename__ = "cc_fp_status"

    __table_args__ = (CheckConstraint("hide_after_days IS NULL OR hide_after_days > 0"),)

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    board_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    on_board: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    hide_after_days: Mapped[int | None] = mapped_column(SmallInteger)


class CCFpCallResult(Base):
    __tablename__ = "cc_fp_call_result"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    counts_toward_limit_default: Mapped[bool] = mapped_column(Boolean, nullable=False)
    next_status: Mapped[str | None] = mapped_column(String(40), ForeignKey("cc_fp_status.code"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class CCFpAgent(Base):
    __tablename__ = "cc_fp_agent"

    __table_args__ = (CheckConstraint("max_open IS NULL OR max_open > 0"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    accepting_new: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    max_open: Mapped[int | None] = mapped_column(SmallInteger)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[int | None] = _user_fk()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# ---------------------------------------------------------------------
# 2. Acuity reference data
# ---------------------------------------------------------------------


class CCFpAcuityCalendar(Base):
    __tablename__ = "cc_fp_acuity_calendar"

    __table_args__ = (Index("ix_cc_fp_acuity_calendar_active_name", "is_active", "name"),)

    acuity_calendar_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    first_synced_at: Mapped[datetime] = _created_at()
    last_synced_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpAcuityAppointmentType(Base):
    __tablename__ = "cc_fp_acuity_appointment_type"

    __table_args__ = (Index("ix_cc_fp_acuity_appointment_type_active_name", "is_active", "name"),)

    acuity_appointment_type_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(150))
    duration_min: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    description: Mapped[str | None] = mapped_column(Text)
    scheduling_url: Mapped[str | None] = mapped_column(Text)
    is_private: Mapped[bool | None] = mapped_column(Boolean)
    # The intake form that holds this exam's "Reason for the appointment"
    # dropdown, that field's id, and its options in Acuity's order (filled by
    # the Acuity sync from GET /forms).
    acuity_form_id: Mapped[int | None] = mapped_column(BigInteger)
    reason_field_id: Mapped[int | None] = mapped_column(BigInteger)
    reason_options: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    first_synced_at: Mapped[datetime] = _created_at()
    last_synced_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpAcuityAppointmentTypeCalendar(Base):
    __tablename__ = "cc_fp_acuity_appointment_type_calendar"

    __table_args__ = (
        Index(
            "ix_cc_fp_acuity_appointment_type_calendar_calendar",
            "acuity_calendar_id",
        ),
    )

    acuity_appointment_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_fp_acuity_appointment_type.acuity_appointment_type_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    acuity_calendar_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_acuity_calendar.acuity_calendar_id", ondelete="CASCADE"),
        primary_key=True,
    )


class CCFpAcuitySyncRun(Base):
    __tablename__ = "cc_fp_acuity_sync_run"

    __table_args__ = (
        CheckConstraint("trigger IN ('scheduled','manual')"),
        CheckConstraint("status IN ('running','success','failed')"),
        Index("ix_cc_fp_acuity_sync_run_started_at", text("started_at DESC")),
    )

    id: Mapped[int] = _pk()
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    triggered_by_user_id: Mapped[int | None] = _user_fk()
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    calendars_added: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    calendars_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    calendars_removed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    types_added: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    types_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    types_removed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    changes: Mapped[list[Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------
# 3. Ingest
# ---------------------------------------------------------------------


class CCFpFax(Base):
    """One row per inbound fax (Comprehend Job ID)."""

    __tablename__ = "cc_fp_fax"

    __table_args__ = (
        UniqueConstraint("job_id", name="uq_cc_fp_fax_job_id"),
        Index("ix_cc_fp_fax_received_at", text("received_at DESC")),
        Index("ix_cc_fp_fax_originating_e164", "originating_e164"),
    )

    id: Mapped[int] = _pk()
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(64))
    term_number: Mapped[str | None] = mapped_column(String(32))
    originating_number: Mapped[str | None] = mapped_column(String(32))
    originating_e164: Mapped[str | None] = mapped_column(String(16))
    result: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[int | None] = mapped_column(SmallInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_name: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    is_spam: Mapped[bool | None] = mapped_column(Boolean)
    is_urgent: Mapped[bool | None] = mapped_column(Boolean)
    summary: Mapped[str | None] = mapped_column(Text)
    is_multi_patient: Mapped[bool | None] = mapped_column(Boolean)
    patient_count: Mapped[int | None] = mapped_column(SmallInteger)
    diagnoses_raw: Mapped[str | None] = mapped_column(Text)
    diagnostic_studies_raw: Mapped[str | None] = mapped_column(Text)
    page_barcodes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    gcs_uri: Mapped[str | None] = mapped_column(Text)
    gcs_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpComprehendPayload(Base):
    """Every Comprehend webhook delivery exactly as received (retries too)."""

    __tablename__ = "cc_fp_comprehend_payload"

    __table_args__ = (Index("ix_cc_fp_comprehend_payload_job_id", "job_id"),)

    id: Mapped[int] = _pk()
    job_id: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = _created_at()
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_error: Mapped[str | None] = mapped_column(Text)
    fax_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_fax.id", ondelete="SET NULL"),
    )


# ---------------------------------------------------------------------
# 4. What Comprehend extracted
# ---------------------------------------------------------------------


class CCFpFaxPatient(Base):
    __tablename__ = "cc_fp_fax_patient"

    __table_args__ = (UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_patient_fax_idx"),)

    id: Mapped[int] = _pk()
    fax_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
        nullable=False,
    )
    idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    dob: Mapped[date | None] = mapped_column(Date)
    dob_raw: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(String(20))
    mrn: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(32))
    phone_e164: Mapped[str | None] = mapped_column(String(16))


class CCFpProvider(Base):
    """Deduplicated referring-provider directory."""

    __tablename__ = "cc_fp_provider"

    __table_args__ = (
        UniqueConstraint("npi", name="uq_cc_fp_provider_npi"),
        Index("ix_cc_fp_provider_fax_e164", "fax_e164"),
    )

    id: Mapped[int] = _pk()
    npi: Mapped[str | None] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    organization: Mapped[str | None] = mapped_column(Text)
    specialty: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(32))
    fax_e164: Mapped[str | None] = mapped_column(String(16))
    fax_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fax_verified_by_user_id: Mapped[int | None] = _user_fk()
    email: Mapped[str | None] = mapped_column(String(255))
    address1: Mapped[str | None] = mapped_column(Text)
    address2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(50))
    zip: Mapped[str | None] = mapped_column(String(20))
    first_seen_at: Mapped[datetime] = _created_at()
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpFaxProvider(Base):
    __tablename__ = "cc_fp_fax_provider"

    __table_args__ = (
        UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_provider_fax_idx"),
        ForeignKeyConstraint(
            ["fax_id", "patient_idx"],
            ["cc_fp_fax_patient.fax_id", "cc_fp_fax_patient.idx"],
        ),
        Index("ix_cc_fp_fax_provider_npi", "npi"),
    )

    id: Mapped[int] = _pk()
    fax_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
        nullable=False,
    )
    idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    role_legacy: Mapped[str | None] = mapped_column(Text)
    patient_idx: Mapped[int | None] = mapped_column(SmallInteger)
    npi: Mapped[str | None] = mapped_column(String(10))
    phone: Mapped[str | None] = mapped_column(String(32))
    address1: Mapped[str | None] = mapped_column(Text)
    address2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(50))
    zip: Mapped[str | None] = mapped_column(String(20))
    fax: Mapped[str | None] = mapped_column(String(32))
    fax_e164: Mapped[str | None] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(255))
    specialty: Mapped[str | None] = mapped_column(String(150))
    is_scanx: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    provider_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_provider.id", ondelete="SET NULL"),
    )


class CCFpFaxDocument(Base):
    __tablename__ = "cc_fp_fax_document"

    __table_args__ = (
        UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_document_fax_idx"),
        ForeignKeyConstraint(
            ["fax_id", "patient_idx"],
            ["cc_fp_fax_patient.fax_id", "cc_fp_fax_patient.idx"],
        ),
        ForeignKeyConstraint(
            ["fax_id", "provider_idx"],
            ["cc_fp_fax_provider.fax_id", "cc_fp_fax_provider.idx"],
        ),
    )

    id: Mapped[int] = _pk()
    fax_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
        nullable=False,
    )
    idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(50))
    doc_date: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(SmallInteger)
    page_end: Mapped[int | None] = mapped_column(SmallInteger)
    patient_idx: Mapped[int | None] = mapped_column(SmallInteger)
    provider_idx: Mapped[int | None] = mapped_column(SmallInteger)


class CCFpFaxInsurance(Base):
    __tablename__ = "cc_fp_fax_insurance"

    __table_args__ = (
        UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_insurance_fax_idx"),
        ForeignKeyConstraint(
            ["fax_id", "patient_idx"],
            ["cc_fp_fax_patient.fax_id", "cc_fp_fax_patient.idx"],
        ),
    )

    id: Mapped[int] = _pk()
    fax_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
        nullable=False,
    )
    idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patient_idx: Mapped[int | None] = mapped_column(SmallInteger)
    payer_name: Mapped[str | None] = mapped_column(Text)
    plan_name: Mapped[str | None] = mapped_column(Text)
    plan_type: Mapped[str | None] = mapped_column(String(50))
    network_name: Mapped[str | None] = mapped_column(Text)
    group_name: Mapped[str | None] = mapped_column(Text)
    group_number: Mapped[str | None] = mapped_column(String(64))
    member_id: Mapped[str | None] = mapped_column(String(64))
    subscriber_name: Mapped[str | None] = mapped_column(Text)
    patient_name_on_card: Mapped[str | None] = mapped_column(Text)
    relationship_to_subscriber: Mapped[str | None] = mapped_column(String(50))
    pcp_name: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    termination_date: Mapped[date | None] = mapped_column(Date)
    source_page_start: Mapped[int | None] = mapped_column(SmallInteger)
    source_page_end: Mapped[int | None] = mapped_column(SmallInteger)
    payer_info_raw: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    benefits: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


# ---------------------------------------------------------------------
# 5. The referral (board card)
# ---------------------------------------------------------------------


class CCFpReferralBoard(Base):
    """One board card: one per patient on a fax."""

    __tablename__ = "cc_fp_referral_board"

    __table_args__ = (
        CheckConstraint("assignment_source IN ('auto','manual')"),
        CheckConstraint("booked_source IN ('agent','acuity_webhook','nightly_check','drag')"),
        CheckConstraint("qualifying_call_count BETWEEN 0 AND 5"),
        Index("ix_cc_fp_referral_board_status_created_at", "status", text("created_at DESC")),
        Index("ix_cc_fp_referral_board_updated_at", "updated_at"),
        Index(
            "ix_cc_fp_referral_board_next_action_at",
            "next_action_at",
            postgresql_where=text("next_action_at IS NOT NULL"),
        ),
        Index("ix_cc_fp_referral_board_phone_e164", "phone_e164"),
        Index("ix_cc_fp_referral_board_fax_id", "fax_id"),
        Index("ix_cc_fp_referral_board_assigned_user_id", "assigned_user_id"),
        Index("ix_cc_fp_referral_board_status_changed_at", "status", "status_changed_at"),
        Index("ix_cc_fp_referral_board_acuity_calendar_id", "acuity_calendar_id"),
        Index(
            "uq_cc_fp_referral_board_acuity_appointment_id",
            "acuity_appointment_id",
            unique=True,
            postgresql_where=text("acuity_appointment_id IS NOT NULL"),
        ),
        Index(
            "uq_cc_fp_referral_board_fax_patient_id",
            "fax_patient_id",
            unique=True,
            postgresql_where=text("fax_patient_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = _pk()
    fax_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cc_fp_fax.id"), nullable=False)
    fax_patient_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_fp_fax_patient.id")
    )
    provider_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_fp_provider.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("cc_fp_status.code"),
        nullable=False,
        server_default="review_required",
    )
    status_changed_at: Mapped[datetime] = _created_at()
    first_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_viewed_by_user_id: Mapped[int | None] = _user_fk()
    is_urgent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    assigned_user_id: Mapped[int | None] = _user_fk()
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignment_source: Mapped[str | None] = mapped_column(String(20))

    # Acuity booking-form fields (coupon and notes left out).
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20))
    phone_e164: Mapped[str | None] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(150))
    gender: Mapped[str | None] = mapped_column(String(10))
    dob: Mapped[date | None] = mapped_column(Date)
    # The patient's address: copied from the fax ("PHI Address") when the card is
    # created, then correctable in Intake Review. Not an Acuity booking-form field.
    address: Mapped[str | None] = mapped_column(Text)
    acuity_calendar_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_fp_acuity_calendar.acuity_calendar_id")
    )
    acuity_calendar_name: Mapped[str | None] = mapped_column(String(150))
    reason: Mapped[str | None] = mapped_column(String(255))
    reason_other: Mapped[str | None] = mapped_column(Text)
    physician_name: Mapped[str | None] = mapped_column(String(255))
    physician_fax_no: Mapped[str | None] = mapped_column(String(255))
    physician_email: Mapped[str | None] = mapped_column(String(255))

    # Review outcome.
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_user_id: Mapped[int | None] = _user_fk()
    not_referral_reason: Mapped[str | None] = mapped_column(Text)
    not_referral_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_referral_by_user_id: Mapped[int | None] = _user_fk()

    # Outreach (cached; source of truth is cc_fp_call_log).
    qualifying_call_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    total_call_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_type: Mapped[str | None] = mapped_column(String(20))
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Conversion: same value as appointment.appointment_id. No FK.
    acuity_appointment_id: Mapped[str | None] = mapped_column(String(50))
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booked_source: Mapped[str | None] = mapped_column(String(20))
    booked_by_user_id: Mapped[int | None] = _user_fk()

    duplicate_of_referral_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_fp_referral_board.id")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpReferralExam(Base):
    __tablename__ = "cc_fp_referral_exam"

    __table_args__ = (
        CheckConstraint("slot IN (1, 2)"),
        UniqueConstraint("referral_id", "slot", name="uq_cc_fp_referral_exam_referral_slot"),
    )

    id: Mapped[int] = _pk()
    referral_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_referral_board.id", ondelete="CASCADE"),
        nullable=False,
    )
    slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    acuity_appointment_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_acuity_appointment_type.acuity_appointment_type_id"),
        nullable=False,
    )
    appointment_type: Mapped[str] = mapped_column(String(255), nullable=False)
    fax_text: Mapped[str | None] = mapped_column(Text)


class CCFpReferralStatusHistory(Base):
    __tablename__ = "cc_fp_referral_status_history"

    __table_args__ = (
        CheckConstraint(
            "source IN ('ingest','button','drag','call_result',"
            "'acuity_webhook','nightly_check','scheduler')"
        ),
        Index("ix_cc_fp_referral_status_history_referral", "referral_id", "changed_at"),
        Index("ix_cc_fp_referral_status_history_changed_at", "changed_at"),
    )

    id: Mapped[int] = _pk()
    referral_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cc_fp_referral_board.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(40), ForeignKey("cc_fp_status.code"))
    to_status: Mapped[str] = mapped_column(
        String(40), ForeignKey("cc_fp_status.code"), nullable=False
    )
    changed_at: Mapped[datetime] = _created_at()
    changed_by_user_id: Mapped[int | None] = _user_fk()
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------
# 6. Outreach
# ---------------------------------------------------------------------


class CCFpCallLog(Base):
    __tablename__ = "cc_fp_call_log"

    __table_args__ = (
        CheckConstraint("direction IN ('outbound','inbound')"),
        CheckConstraint("result IS DISTINCT FROM 'other' OR other_reason IS NOT NULL"),
        Index("ix_cc_fp_call_log_referral", "referral_id", "started_at"),
        Index("ix_cc_fp_call_log_user", "user_id", "started_at"),
        Index(
            "uq_cc_fp_call_log_twilio_call_sid",
            "twilio_call_sid",
            unique=True,
            postgresql_where=text("twilio_call_sid IS NOT NULL"),
        ),
    )

    id: Mapped[int] = _pk()
    referral_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cc_fp_referral_board.id"), nullable=False
    )
    user_id: Mapped[int | None] = _user_fk()
    direction: Mapped[str] = mapped_column(String(20), nullable=False, server_default="outbound")
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    from_number: Mapped[str | None] = mapped_column(String(20))
    twilio_call_sid: Mapped[str | None] = mapped_column(String(100))
    twilio_status: Mapped[str | None] = mapped_column(String(50))
    started_at: Mapped[datetime] = _created_at()
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(40), ForeignKey("cc_fp_call_result.code"))
    other_reason: Mapped[str | None] = mapped_column(Text)
    counts_toward_limit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    attempt_number: Mapped[int | None] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(Text)
    call_log_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpMessageLog(Base):
    __tablename__ = "cc_fp_message_log"

    __table_args__ = (
        CheckConstraint("direction IN ('outbound','inbound')"),
        CheckConstraint("channel IN ('rcs','sms')"),
        Index("ix_cc_fp_message_log_referral", "referral_id", "created_at"),
        Index("ix_cc_fp_message_log_to_number", "to_number"),
        Index(
            "uq_cc_fp_message_log_twilio_message_sid",
            "twilio_message_sid",
            unique=True,
            postgresql_where=text("twilio_message_sid IS NOT NULL"),
        ),
    )

    id: Mapped[int] = _pk()
    referral_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_fp_referral_board.id")
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False, server_default="outbound")
    channel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="rcs")
    user_id: Mapped[int | None] = _user_fk()
    template_code: Mapped[str | None] = mapped_column(String(50))
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    from_number: Mapped[str | None] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    rich_content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    twilio_message_sid: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="queued")
    status_at: Mapped[datetime] = _created_at()
    error_code: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CCFpContactOptout(Base):
    __tablename__ = "cc_fp_contact_optout"

    phone_e164: Mapped[str] = mapped_column(String(16), primary_key=True)
    sms_opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dnc_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(30))
    updated_by_user_id: Mapped[int | None] = _user_fk()
    updated_at: Mapped[datetime] = _updated_at()


# ---------------------------------------------------------------------
# 7. Audit
# ---------------------------------------------------------------------


class CCFpActivityLog(Base):
    """Append-only: who did what, in which section."""

    __tablename__ = "cc_fp_activity_log"

    __table_args__ = (
        CheckConstraint(
            "section IN ('intake_review','call_queue','board','referral_case','settings','system')"
        ),
        Index("ix_cc_fp_activity_log_referral", "referral_id", "occurred_at"),
        Index("ix_cc_fp_activity_log_user", "user_id", "occurred_at"),
        Index("ix_cc_fp_activity_log_section", "section", "occurred_at"),
    )

    id: Mapped[int] = _pk()
    occurred_at: Mapped[datetime] = _created_at()
    user_id: Mapped[int | None] = _user_fk()
    section: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    referral_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cc_fp_referral_board.id")
    )
    fax_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cc_fp_fax.id"))
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
