"""create cc_call_logs, Command Center's own record of Calls module calls

Self-contained: no existing table is altered; the only foreign key is the user
column referencing cc_users(id). The production `call_logs` row the call is
mirrored to is a plain column (prod_call_log_id), not a foreign key.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_call_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("cc_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("direction", sa.String(length=20), server_default="outbound", nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=False),
        sa.Column("appointment_id", sa.String(length=50), nullable=True),
        sa.Column("twilio_call_sid", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="queued", nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("prod_call_log_id", sa.Integer(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("direction IN ('outbound','inbound')"),
    )

    op.create_index("ix_cc_call_logs_user", "cc_call_logs", ["user_id", "started_at"])
    op.create_index("ix_cc_call_logs_prod_call_log_id", "cc_call_logs", ["prod_call_log_id"])
    op.create_index(
        "uq_cc_call_logs_twilio_call_sid",
        "cc_call_logs",
        ["twilio_call_sid"],
        unique=True,
        postgresql_where=sa.text("twilio_call_sid IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_cc_call_logs_twilio_call_sid", table_name="cc_call_logs")
    op.drop_index("ix_cc_call_logs_prod_call_log_id", table_name="cc_call_logs")
    op.drop_index("ix_cc_call_logs_user", table_name="cc_call_logs")
    op.drop_table("cc_call_logs")
