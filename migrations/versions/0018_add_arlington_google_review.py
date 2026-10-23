"""add the Google review link for ScanX Arlington

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-23

Migration 0014 seeded review links for the first two clinics (1 = Dallas,
2 = Fairview). Arlington is clinic 3 in the production clinics table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARLINGTON_CLINIC_ID = 3
ARLINGTON_REVIEW_URL = "https://g.page/r/CTNSToyJlea_EBM/review"


def upgrade() -> None:
    # A link already set for the clinic (by hand) is left as it is.
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO cc_google_reviews (clinic_id, google_review_url)
            VALUES (:clinic_id, :url)
            ON CONFLICT (clinic_id) DO NOTHING
            """
        ),
        {"clinic_id": ARLINGTON_CLINIC_ID, "url": ARLINGTON_REVIEW_URL},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM cc_google_reviews "
            "WHERE clinic_id = :clinic_id AND google_review_url = :url"
        ),
        {"clinic_id": ARLINGTON_CLINIC_ID, "url": ARLINGTON_REVIEW_URL},
    )
