"""create report links table

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_report_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("gcs_blob_name", sa.String(length=512), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_report_links_appointment_id",
        "cc_report_links",
        ["appointment_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_report_links_token",
        "cc_report_links",
        ["token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cc_report_links_token",
        table_name="cc_report_links",
    )
    op.drop_index(
        "ix_cc_report_links_appointment_id",
        table_name="cc_report_links",
    )
    op.drop_table("cc_report_links")
