"""add the "Reason for the appointment" options to cc_fp_acuity_appointment_type

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-16

Each exam's Acuity booking form has its own "Reason for the appointment"
dropdown. The Acuity sync (GET /forms) now keeps, per exam, the form that holds
it, the field id and the options in Acuity's order, so Intake Review can offer
the same choices. Adds three columns to the Fax Portal's own table; the card's
`reason` and `reason_other` columns already exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cc_fp_acuity_appointment_type", sa.Column("acuity_form_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "cc_fp_acuity_appointment_type",
        sa.Column("reason_field_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "cc_fp_acuity_appointment_type",
        sa.Column(
            "reason_options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cc_fp_acuity_appointment_type", "reason_options")
    op.drop_column("cc_fp_acuity_appointment_type", "reason_field_id")
    op.drop_column("cc_fp_acuity_appointment_type", "acuity_form_id")
