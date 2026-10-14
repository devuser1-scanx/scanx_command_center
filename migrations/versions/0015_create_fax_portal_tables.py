"""create fax portal (cc_fp_) tables, views and seed rows

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-14

Source of truth: docs/schema.sql (draft v3.2). Self-contained: no existing
table is altered; the only foreign keys to existing tables are user columns
referencing cc_users(id).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pk() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False)


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def _user(name: str, *, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name,
        sa.BigInteger(),
        sa.ForeignKey("cc_users.id", ondelete=ondelete),
        nullable=True,
    )


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Lookups
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_status",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("board_order", sa.SmallInteger(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("on_board", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("hide_after_days", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint("hide_after_days IS NULL OR hide_after_days > 0"),
    )

    op.execute(
        """
        INSERT INTO cc_fp_status
            (code, label, board_order, is_open, on_board, hide_after_days)
        VALUES
            ('review_required',           'Review Required',           10, TRUE,  TRUE,  NULL),
            ('ready_for_contact',         'Ready for Contact',         20, TRUE,  TRUE,  NULL),
            ('follow_up_due',             'Follow-up Due',             30, TRUE,  TRUE,  NULL),
            ('callback_requested',        'Callback Requested',        40, TRUE,  TRUE,  NULL),
            ('fax_provider_name_pending', 'Fax Provider Name Pending', 50, TRUE,  TRUE,  NULL),
            ('didnt_respond',             'Didn''t Respond',           60, FALSE, TRUE,  30),
            ('appointment_booked',        'Appointment Booked',        70, FALSE, TRUE,  14),
            ('not_referral',              'Not Referrals',             80, FALSE, TRUE,  7),
            ('declined',                  'Declined',                  90, FALSE, FALSE, NULL),
            ('exception',                 'Exception',                 95, TRUE,  TRUE,  NULL)
        """
    )

    op.create_table(
        "cc_fp_call_result",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("counts_toward_limit_default", sa.Boolean(), nullable=False),
        sa.Column("next_status", sa.String(40), sa.ForeignKey("cc_fp_status.code")),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
    )

    op.execute(
        """
        INSERT INTO cc_fp_call_result
            (code, label, counts_toward_limit_default, next_status, sort_order)
        VALUES
            ('booked',             'Booked appointment',     FALSE, 'appointment_booked', 10),
            ('no_answer',          'No answer',              TRUE,  'follow_up_due',      20),
            ('voicemail',          'Left voicemail',         TRUE,  'follow_up_due',      30),
            ('busy',               'Busy',                   TRUE,  'follow_up_due',      40),
            ('failed',             'Call failed / dropped',  TRUE,  'follow_up_due',      50),
            ('callback_requested', 'Callback requested',     FALSE, 'callback_requested', 60),
            ('declined',           'Declined',               FALSE, 'declined',           70),
            ('wrong_number',       'Wrong number',           FALSE, 'exception',          80),
            ('other',              'Other',                  FALSE, NULL,                 90)
        """
    )

    op.create_table(
        "cc_fp_agent",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("accepting_new", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("max_open", sa.SmallInteger(), nullable=True),
        _ts("last_assigned_at", nullable=True, default=False),
        _user("updated_by_user_id"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("max_open IS NULL OR max_open > 0"),
    )

    # -----------------------------------------------------------------
    # 2. Acuity reference data
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_acuity_calendar",
        sa.Column("acuity_calendar_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("location", sa.Text()),
        sa.Column("timezone", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _ts("removed_at", nullable=True, default=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        _ts("first_synced_at"),
        _ts("last_synced_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_cc_fp_acuity_calendar_active_name", "cc_fp_acuity_calendar", ["is_active", "name"]
    )

    op.create_table(
        "cc_fp_acuity_appointment_type",
        sa.Column(
            "acuity_appointment_type_id", sa.BigInteger(), primary_key=True, autoincrement=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(150)),
        sa.Column("duration_min", sa.Integer()),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("description", sa.Text()),
        sa.Column("scheduling_url", sa.Text()),
        sa.Column("is_private", sa.Boolean()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _ts("removed_at", nullable=True, default=False),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        _ts("first_synced_at"),
        _ts("last_synced_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_cc_fp_acuity_appointment_type_active_name",
        "cc_fp_acuity_appointment_type",
        ["is_active", "name"],
    )

    op.create_table(
        "cc_fp_acuity_appointment_type_calendar",
        sa.Column(
            "acuity_appointment_type_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "cc_fp_acuity_appointment_type.acuity_appointment_type_id",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "acuity_calendar_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_acuity_calendar.acuity_calendar_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_cc_fp_acuity_appointment_type_calendar_calendar",
        "cc_fp_acuity_appointment_type_calendar",
        ["acuity_calendar_id"],
    )

    op.create_table(
        "cc_fp_acuity_sync_run",
        _pk(),
        sa.Column("trigger", sa.String(20), nullable=False),
        _user("triggered_by_user_id"),
        _ts("started_at"),
        _ts("finished_at", nullable=True, default=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("calendars_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calendars_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calendars_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("types_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("types_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("types_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changes", postgresql.JSONB()),
        sa.Column("error_message", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("trigger IN ('scheduled','manual')"),
        sa.CheckConstraint("status IN ('running','success','failed')"),
    )
    op.create_index(
        "ix_cc_fp_acuity_sync_run_started_at",
        "cc_fp_acuity_sync_run",
        [sa.text("started_at DESC")],
    )

    # -----------------------------------------------------------------
    # 3. Ingest
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_fax",
        _pk(),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(64)),
        sa.Column("term_number", sa.String(32)),
        sa.Column("originating_number", sa.String(32)),
        sa.Column("originating_e164", sa.String(16)),
        sa.Column("result", sa.String(32)),
        sa.Column("page_count", sa.SmallInteger()),
        _ts("received_at", default=False),
        sa.Column("job_name", sa.Text()),
        sa.Column("contact_name", sa.Text()),
        sa.Column("is_spam", sa.Boolean()),
        sa.Column("is_urgent", sa.Boolean()),
        sa.Column("summary", sa.Text()),
        sa.Column("is_multi_patient", sa.Boolean()),
        sa.Column("patient_count", sa.SmallInteger()),
        sa.Column("diagnoses_raw", sa.Text()),
        sa.Column("diagnostic_studies_raw", sa.Text()),
        sa.Column("page_barcodes", postgresql.ARRAY(sa.Text())),
        sa.Column("gcs_uri", sa.Text()),
        _ts("gcs_received_at", nullable=True, default=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_cc_fp_fax_job_id"),
    )
    op.create_index("ix_cc_fp_fax_received_at", "cc_fp_fax", [sa.text("received_at DESC")])
    op.create_index("ix_cc_fp_fax_originating_e164", "cc_fp_fax", ["originating_e164"])

    op.create_table(
        "cc_fp_comprehend_payload",
        _pk(),
        sa.Column("job_id", sa.String(64)),
        _ts("received_at"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        _ts("parsed_at", nullable=True, default=False),
        sa.Column("parse_error", sa.Text()),
        sa.Column(
            "fax_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_fax.id", ondelete="SET NULL"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cc_fp_comprehend_payload_job_id", "cc_fp_comprehend_payload", ["job_id"])

    # -----------------------------------------------------------------
    # 4. What Comprehend extracted
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_fax_patient",
        _pk(),
        sa.Column(
            "fax_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("full_name", sa.Text()),
        sa.Column("dob", sa.Date()),
        sa.Column("dob_raw", sa.String(64)),
        sa.Column("address", sa.Text()),
        sa.Column("gender", sa.String(20)),
        sa.Column("mrn", sa.String(64)),
        sa.Column("phone", sa.String(32)),
        sa.Column("phone_e164", sa.String(16)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_patient_fax_idx"),
    )

    # cc_fp_provider before cc_fp_fax_provider (which references it).
    op.create_table(
        "cc_fp_provider",
        _pk(),
        sa.Column("npi", sa.String(10)),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text()),
        sa.Column("specialty", sa.String(150)),
        sa.Column("phone", sa.String(32)),
        sa.Column("fax_e164", sa.String(16)),
        _ts("fax_verified_at", nullable=True, default=False),
        _user("fax_verified_by_user_id"),
        sa.Column("email", sa.String(255)),
        sa.Column("address1", sa.Text()),
        sa.Column("address2", sa.Text()),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(50)),
        sa.Column("zip", sa.String(20)),
        _ts("first_seen_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("npi", name="uq_cc_fp_provider_npi"),
    )
    op.create_index("ix_cc_fp_provider_fax_e164", "cc_fp_provider", ["fax_e164"])

    op.create_table(
        "cc_fp_fax_provider",
        _pk(),
        sa.Column(
            "fax_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column(
            "roles",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("role_legacy", sa.Text()),
        sa.Column("patient_idx", sa.SmallInteger()),
        sa.Column("npi", sa.String(10)),
        sa.Column("phone", sa.String(32)),
        sa.Column("address1", sa.Text()),
        sa.Column("address2", sa.Text()),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(50)),
        sa.Column("zip", sa.String(20)),
        sa.Column("fax", sa.String(32)),
        sa.Column("fax_e164", sa.String(16)),
        sa.Column("email", sa.String(255)),
        sa.Column("specialty", sa.String(150)),
        sa.Column("is_scanx", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "provider_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_provider.id", ondelete="SET NULL"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_provider_fax_idx"),
        sa.ForeignKeyConstraint(
            ["fax_id", "patient_idx"],
            ["cc_fp_fax_patient.fax_id", "cc_fp_fax_patient.idx"],
        ),
    )
    op.create_index("ix_cc_fp_fax_provider_npi", "cc_fp_fax_provider", ["npi"])

    op.create_table(
        "cc_fp_fax_document",
        _pk(),
        sa.Column(
            "fax_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("doc_type", sa.String(50)),
        sa.Column("doc_date", sa.Date()),
        sa.Column("summary", sa.Text()),
        sa.Column("page_start", sa.SmallInteger()),
        sa.Column("page_end", sa.SmallInteger()),
        sa.Column("patient_idx", sa.SmallInteger()),
        sa.Column("provider_idx", sa.SmallInteger()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_document_fax_idx"),
        sa.ForeignKeyConstraint(
            ["fax_id", "patient_idx"],
            ["cc_fp_fax_patient.fax_id", "cc_fp_fax_patient.idx"],
        ),
        sa.ForeignKeyConstraint(
            ["fax_id", "provider_idx"],
            ["cc_fp_fax_provider.fax_id", "cc_fp_fax_provider.idx"],
        ),
    )

    op.create_table(
        "cc_fp_fax_insurance",
        _pk(),
        sa.Column(
            "fax_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_fax.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("patient_idx", sa.SmallInteger()),
        sa.Column("payer_name", sa.Text()),
        sa.Column("plan_name", sa.Text()),
        sa.Column("plan_type", sa.String(50)),
        sa.Column("network_name", sa.Text()),
        sa.Column("group_name", sa.Text()),
        sa.Column("group_number", sa.String(64)),
        sa.Column("member_id", sa.String(64)),
        sa.Column("subscriber_name", sa.Text()),
        sa.Column("patient_name_on_card", sa.Text()),
        sa.Column("relationship_to_subscriber", sa.String(50)),
        sa.Column("pcp_name", sa.Text()),
        sa.Column("effective_date", sa.Date()),
        sa.Column("termination_date", sa.Date()),
        sa.Column("source_page_start", sa.SmallInteger()),
        sa.Column("source_page_end", sa.SmallInteger()),
        sa.Column("payer_info_raw", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "benefits",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fax_id", "idx", name="uq_cc_fp_fax_insurance_fax_idx"),
        sa.ForeignKeyConstraint(
            ["fax_id", "patient_idx"],
            ["cc_fp_fax_patient.fax_id", "cc_fp_fax_patient.idx"],
        ),
    )

    # -----------------------------------------------------------------
    # 5. The referral (board card)
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_referral_board",
        _pk(),
        sa.Column("fax_id", sa.BigInteger(), sa.ForeignKey("cc_fp_fax.id"), nullable=False),
        sa.Column("fax_patient_id", sa.BigInteger(), sa.ForeignKey("cc_fp_fax_patient.id")),
        sa.Column(
            "provider_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_provider.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "status",
            sa.String(40),
            sa.ForeignKey("cc_fp_status.code"),
            nullable=False,
            server_default="review_required",
        ),
        _ts("status_changed_at"),
        _ts("first_viewed_at", nullable=True, default=False),
        _user("first_viewed_by_user_id"),
        sa.Column("is_urgent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _user("assigned_user_id"),
        _ts("assigned_at", nullable=True, default=False),
        sa.Column("assignment_source", sa.String(20)),
        sa.Column("first_name", sa.String(100)),
        sa.Column("last_name", sa.String(100)),
        sa.Column("phone", sa.String(20)),
        sa.Column("phone_e164", sa.String(16)),
        sa.Column("email", sa.String(150)),
        sa.Column("gender", sa.String(10)),
        sa.Column("dob", sa.Date()),
        sa.Column(
            "acuity_calendar_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_acuity_calendar.acuity_calendar_id"),
        ),
        sa.Column("acuity_calendar_name", sa.String(150)),
        sa.Column("reason", sa.String(255)),
        sa.Column("reason_other", sa.Text()),
        sa.Column("physician_name", sa.String(255)),
        sa.Column("physician_fax_no", sa.String(255)),
        sa.Column("physician_email", sa.String(255)),
        _ts("released_at", nullable=True, default=False),
        _user("released_by_user_id"),
        sa.Column("not_referral_reason", sa.Text()),
        _ts("not_referral_at", nullable=True, default=False),
        _user("not_referral_by_user_id"),
        sa.Column(
            "qualifying_call_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "total_call_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        _ts("last_contact_at", nullable=True, default=False),
        _ts("next_action_at", nullable=True, default=False),
        sa.Column("next_action_type", sa.String(20)),
        _ts("callback_at", nullable=True, default=False),
        sa.Column("acuity_appointment_id", sa.String(50)),
        _ts("booked_at", nullable=True, default=False),
        sa.Column("booked_source", sa.String(20)),
        _user("booked_by_user_id"),
        sa.Column(
            "duplicate_of_referral_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_referral_board.id"),
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("assignment_source IN ('auto','manual')"),
        sa.CheckConstraint("booked_source IN ('agent','acuity_webhook','nightly_check','drag')"),
        sa.CheckConstraint("qualifying_call_count BETWEEN 0 AND 5"),
    )
    op.create_index(
        "ix_cc_fp_referral_board_status_created_at",
        "cc_fp_referral_board",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index("ix_cc_fp_referral_board_updated_at", "cc_fp_referral_board", ["updated_at"])
    op.create_index(
        "ix_cc_fp_referral_board_next_action_at",
        "cc_fp_referral_board",
        ["next_action_at"],
        postgresql_where=sa.text("next_action_at IS NOT NULL"),
    )
    op.create_index("ix_cc_fp_referral_board_phone_e164", "cc_fp_referral_board", ["phone_e164"])
    op.create_index("ix_cc_fp_referral_board_fax_id", "cc_fp_referral_board", ["fax_id"])
    op.create_index(
        "ix_cc_fp_referral_board_assigned_user_id", "cc_fp_referral_board", ["assigned_user_id"]
    )
    op.create_index(
        "ix_cc_fp_referral_board_status_changed_at",
        "cc_fp_referral_board",
        ["status", "status_changed_at"],
    )
    op.create_index(
        "ix_cc_fp_referral_board_acuity_calendar_id",
        "cc_fp_referral_board",
        ["acuity_calendar_id"],
    )
    op.create_index(
        "uq_cc_fp_referral_board_acuity_appointment_id",
        "cc_fp_referral_board",
        ["acuity_appointment_id"],
        unique=True,
        postgresql_where=sa.text("acuity_appointment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_cc_fp_referral_board_fax_patient_id",
        "cc_fp_referral_board",
        ["fax_patient_id"],
        unique=True,
        postgresql_where=sa.text("fax_patient_id IS NOT NULL"),
    )

    op.create_table(
        "cc_fp_referral_exam",
        _pk(),
        sa.Column(
            "referral_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_referral_board.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column(
            "acuity_appointment_type_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_acuity_appointment_type.acuity_appointment_type_id"),
            nullable=False,
        ),
        sa.Column("appointment_type", sa.String(255), nullable=False),
        sa.Column("fax_text", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("slot IN (1, 2)"),
        sa.UniqueConstraint("referral_id", "slot", name="uq_cc_fp_referral_exam_referral_slot"),
    )

    op.create_table(
        "cc_fp_referral_status_history",
        _pk(),
        sa.Column(
            "referral_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_referral_board.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(40), sa.ForeignKey("cc_fp_status.code")),
        sa.Column("to_status", sa.String(40), sa.ForeignKey("cc_fp_status.code"), nullable=False),
        _ts("changed_at"),
        _user("changed_by_user_id"),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("note", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source IN ('ingest','button','drag','call_result',"
            "'acuity_webhook','nightly_check','scheduler')"
        ),
    )
    op.create_index(
        "ix_cc_fp_referral_status_history_referral",
        "cc_fp_referral_status_history",
        ["referral_id", "changed_at"],
    )
    op.create_index(
        "ix_cc_fp_referral_status_history_changed_at",
        "cc_fp_referral_status_history",
        ["changed_at"],
    )

    # -----------------------------------------------------------------
    # 6. Outreach
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_call_log",
        _pk(),
        sa.Column(
            "referral_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_fp_referral_board.id"),
            nullable=False,
        ),
        _user("user_id"),
        sa.Column("direction", sa.String(20), nullable=False, server_default="outbound"),
        sa.Column("to_number", sa.String(20), nullable=False),
        sa.Column("from_number", sa.String(20)),
        sa.Column("twilio_call_sid", sa.String(100)),
        sa.Column("twilio_status", sa.String(50)),
        _ts("started_at"),
        _ts("answered_at", nullable=True, default=False),
        _ts("ended_at", nullable=True, default=False),
        sa.Column("duration_s", sa.Integer()),
        sa.Column("recording_url", sa.Text()),
        sa.Column("result", sa.String(40), sa.ForeignKey("cc_fp_call_result.code")),
        sa.Column("other_reason", sa.Text()),
        sa.Column(
            "counts_toward_limit", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("attempt_number", sa.SmallInteger()),
        sa.Column("notes", sa.Text()),
        sa.Column("call_log_id", sa.Integer()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("direction IN ('outbound','inbound')"),
        sa.CheckConstraint("result IS DISTINCT FROM 'other' OR other_reason IS NOT NULL"),
    )
    op.create_index("ix_cc_fp_call_log_referral", "cc_fp_call_log", ["referral_id", "started_at"])
    op.create_index("ix_cc_fp_call_log_user", "cc_fp_call_log", ["user_id", "started_at"])
    op.create_index(
        "uq_cc_fp_call_log_twilio_call_sid",
        "cc_fp_call_log",
        ["twilio_call_sid"],
        unique=True,
        postgresql_where=sa.text("twilio_call_sid IS NOT NULL"),
    )

    op.create_table(
        "cc_fp_message_log",
        _pk(),
        sa.Column("referral_id", sa.BigInteger(), sa.ForeignKey("cc_fp_referral_board.id")),
        sa.Column("direction", sa.String(10), nullable=False, server_default="outbound"),
        sa.Column("channel", sa.String(10), nullable=False, server_default="rcs"),
        _user("user_id"),
        sa.Column("template_code", sa.String(50)),
        sa.Column("to_number", sa.String(20), nullable=False),
        sa.Column("from_number", sa.String(20)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("rich_content", postgresql.JSONB()),
        sa.Column("twilio_message_sid", sa.String(64)),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        _ts("status_at"),
        sa.Column("error_code", sa.String(20)),
        sa.Column("error_message", sa.Text()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("direction IN ('outbound','inbound')"),
        sa.CheckConstraint("channel IN ('rcs','sms')"),
    )
    op.create_index(
        "ix_cc_fp_message_log_referral", "cc_fp_message_log", ["referral_id", "created_at"]
    )
    op.create_index("ix_cc_fp_message_log_to_number", "cc_fp_message_log", ["to_number"])
    op.create_index(
        "uq_cc_fp_message_log_twilio_message_sid",
        "cc_fp_message_log",
        ["twilio_message_sid"],
        unique=True,
        postgresql_where=sa.text("twilio_message_sid IS NOT NULL"),
    )

    op.create_table(
        "cc_fp_contact_optout",
        sa.Column("phone_e164", sa.String(16), primary_key=True),
        _ts("sms_opt_out_at", nullable=True, default=False),
        _ts("dnc_at", nullable=True, default=False),
        sa.Column("source", sa.String(30)),
        _user("updated_by_user_id"),
        _ts("updated_at"),
    )

    # -----------------------------------------------------------------
    # 7. Audit
    # -----------------------------------------------------------------
    op.create_table(
        "cc_fp_activity_log",
        _pk(),
        _ts("occurred_at"),
        _user("user_id"),
        sa.Column("section", sa.String(30), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("referral_id", sa.BigInteger(), sa.ForeignKey("cc_fp_referral_board.id")),
        sa.Column("fax_id", sa.BigInteger(), sa.ForeignKey("cc_fp_fax.id")),
        sa.Column("changes", postgresql.JSONB()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "section IN ('intake_review','call_queue','board','referral_case','settings','system')"
        ),
    )
    op.create_index(
        "ix_cc_fp_activity_log_referral", "cc_fp_activity_log", ["referral_id", "occurred_at"]
    )
    op.create_index("ix_cc_fp_activity_log_user", "cc_fp_activity_log", ["user_id", "occurred_at"])
    op.create_index(
        "ix_cc_fp_activity_log_section", "cc_fp_activity_log", ["section", "occurred_at"]
    )

    # -----------------------------------------------------------------
    # 8. Analytics views (no extra tables)
    # -----------------------------------------------------------------
    op.execute(
        """
        CREATE VIEW cc_fp_v_referral_funnel AS
        SELECT
            r.id                                    AS referral_id,
            f.received_at,
            (f.received_at AT TIME ZONE 'America/Chicago')::date AS received_day,
            r.acuity_calendar_id,
            coalesce(ac.name, r.acuity_calendar_name) AS clinic,
            p.name                                  AS referring_provider,
            r.status,
            r.released_at,
            fc.first_call_at,
            r.booked_at,
            r.booked_source,
            r.acuity_appointment_id,
            r.total_call_count,
            r.qualifying_call_count,
            EXTRACT(EPOCH FROM (r.released_at - f.received_at)) / 60 AS minutes_to_release,
            EXTRACT(EPOCH FROM (fc.first_call_at - r.released_at)) / 60 AS minutes_to_first_call
        FROM cc_fp_referral_board r
        JOIN cc_fp_fax f ON f.id = r.fax_id
        LEFT JOIN cc_fp_provider p ON p.id = r.provider_id
        LEFT JOIN cc_fp_acuity_calendar ac ON ac.acuity_calendar_id = r.acuity_calendar_id
        LEFT JOIN LATERAL (
            SELECT min(a.started_at) AS first_call_at
            FROM cc_fp_call_log a WHERE a.referral_id = r.id
        ) fc ON TRUE
        """
    )

    op.execute(
        """
        CREATE VIEW cc_fp_v_daily_clinic AS
        SELECT
            received_day,
            acuity_calendar_id,
            clinic,
            count(*)                                              AS referrals,
            count(*) FILTER (WHERE status = 'not_referral')       AS not_referrals,
            count(*) FILTER (WHERE released_at IS NOT NULL)       AS released,
            count(*) FILTER (WHERE status = 'appointment_booked') AS booked,
            count(*) FILTER (WHERE status = 'didnt_respond')      AS didnt_respond
        FROM cc_fp_v_referral_funnel
        GROUP BY received_day, acuity_calendar_id, clinic
        """
    )

    op.execute(
        """
        CREATE VIEW cc_fp_v_exam_counts AS
        SELECT
            e.acuity_appointment_type_id,
            coalesce(t.name, max(e.appointment_type))            AS exam,
            count(*)                                             AS referrals,
            count(*) FILTER (WHERE r.status = 'appointment_booked') AS booked
        FROM cc_fp_referral_exam e
        JOIN cc_fp_referral_board r ON r.id = e.referral_id
        LEFT JOIN cc_fp_acuity_appointment_type t
            ON t.acuity_appointment_type_id = e.acuity_appointment_type_id
        GROUP BY e.acuity_appointment_type_id, t.name
        """
    )

    op.execute(
        """
        CREATE VIEW cc_fp_v_agent_daily AS
        SELECT
            (a.started_at AT TIME ZONE 'America/Chicago')::date AS day,
            a.user_id,
            u.first_name || ' ' || u.last_name                   AS agent,
            count(*)                                             AS calls,
            count(*) FILTER (WHERE a.answered_at IS NOT NULL)    AS answered,
            count(*) FILTER (WHERE a.result = 'booked')          AS booked,
            count(*) FILTER (WHERE a.counts_toward_limit)        AS qualifying_misses,
            coalesce(sum(a.duration_s), 0)                       AS talk_seconds
        FROM cc_fp_call_log a
        LEFT JOIN cc_users u ON u.id = a.user_id
        GROUP BY 1, 2, 3
        """
    )


def downgrade() -> None:
    # Only cc_fp_ objects are touched, in reverse dependency order.
    op.execute("DROP VIEW IF EXISTS cc_fp_v_agent_daily")
    op.execute("DROP VIEW IF EXISTS cc_fp_v_exam_counts")
    op.execute("DROP VIEW IF EXISTS cc_fp_v_daily_clinic")
    op.execute("DROP VIEW IF EXISTS cc_fp_v_referral_funnel")

    for table in (
        "cc_fp_activity_log",
        "cc_fp_contact_optout",
        "cc_fp_message_log",
        "cc_fp_call_log",
        "cc_fp_referral_status_history",
        "cc_fp_referral_exam",
        "cc_fp_referral_board",
        "cc_fp_fax_insurance",
        "cc_fp_fax_document",
        "cc_fp_fax_provider",
        "cc_fp_provider",
        "cc_fp_fax_patient",
        "cc_fp_comprehend_payload",
        "cc_fp_fax",
        "cc_fp_acuity_sync_run",
        "cc_fp_acuity_appointment_type_calendar",
        "cc_fp_acuity_appointment_type",
        "cc_fp_acuity_calendar",
        "cc_fp_agent",
        "cc_fp_call_result",
        "cc_fp_status",
    ):
        op.drop_table(table)
