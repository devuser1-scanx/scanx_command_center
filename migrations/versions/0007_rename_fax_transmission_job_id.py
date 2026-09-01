"""rename cc_fax_transmissions.westfax_job_id to email_message_id

Fax sending moved from the WestFax REST API to WestFax's email-to-fax
gateway (sent via Gmail), so the job id this column stores is now a Gmail
message id, not a WestFax job id.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "cc_fax_transmissions",
        "westfax_job_id",
        new_column_name="email_message_id",
    )

    op.execute(
        "ALTER INDEX ix_cc_fax_transmissions_westfax_job_id "
        "RENAME TO ix_cc_fax_transmissions_email_message_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_cc_fax_transmissions_email_message_id "
        "RENAME TO ix_cc_fax_transmissions_westfax_job_id"
    )

    op.alter_column(
        "cc_fax_transmissions",
        "email_message_id",
        new_column_name="westfax_job_id",
    )
