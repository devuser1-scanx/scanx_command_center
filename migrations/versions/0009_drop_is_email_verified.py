"""drop cc_users.is_email_verified

Command Center users are admin-created, not self-signup, so an
email-verification gate never mapped to a real control here - the column
was set True at creation and never checked anywhere. Removing it instead
of leaving a dead/inert security-looking field in place.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("cc_users", "is_email_verified")


def downgrade() -> None:
    op.add_column(
        "cc_users",
        sa.Column(
            "is_email_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
