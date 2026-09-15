"""create google reviews table

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_google_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("google_review_url", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_google_reviews_clinic_id",
        "cc_google_reviews",
        ["clinic_id"],
        unique=True,
    )

    connection = op.get_bind()

    # clinic_id 1 = ScanX Dallas, 2 = ScanX Fairview (production clinics
    # table).
    connection.execute(
        sa.text(
            """
            INSERT INTO cc_google_reviews (clinic_id, google_review_url)
            VALUES
                (1, 'https://g.page/r/CQzwReN9wrJrEBM/review'),
                (2, 'https://g.page/r/Cbc0EDh29KduEBM/review')
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cc_google_reviews_clinic_id",
        table_name="cc_google_reviews",
    )
    op.drop_table("cc_google_reviews")
