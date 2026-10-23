"""add the patient's address to cc_fp_referral_board

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-16

Adds one nullable column to the Fax Portal's own referral card table. The
address comes from the fax ("PHI Address", already stored on
cc_fp_fax_patient); existing cards are filled from it so Intake Review shows
it for faxes that arrived before this migration. Edits are made on the card
only: the extracted cc_fp_fax_patient row is never changed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cc_fp_referral_board", sa.Column("address", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE cc_fp_referral_board AS b
           SET address = p.address
          FROM cc_fp_fax_patient AS p
         WHERE p.id = b.fax_patient_id
           AND b.address IS NULL
           AND p.address IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("cc_fp_referral_board", "address")
