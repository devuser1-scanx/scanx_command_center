"""create report link otps table

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_report_link_otps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("otp_id", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_report_link_otps_otp_id",
        "cc_report_link_otps",
        ["otp_id"],
        unique=True,
    )

    op.create_index(
        "ix_cc_report_link_otps_phone",
        "cc_report_link_otps",
        ["phone"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cc_report_link_otps_phone",
        table_name="cc_report_link_otps",
    )
    op.drop_index(
        "ix_cc_report_link_otps_otp_id",
        table_name="cc_report_link_otps",
    )
    op.drop_table("cc_report_link_otps")
