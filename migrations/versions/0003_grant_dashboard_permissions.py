"""grant dashboard/appointments/clinics view permissions to front_desk and sonographer

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_CODES = ["appointments.view", "dashboard.view", "clinics.view"]
ROLE_CODES = ["front_desk", "sonographer"]


def upgrade() -> None:
    connection = op.get_bind()

    role_rows = (
        connection.execute(
            sa.text(
                """
                SELECT id, code
                FROM cc_roles
                WHERE code = ANY(:codes)
                """
            ),
            {"codes": ROLE_CODES},
        )
        .mappings()
        .all()
    )

    permission_rows = (
        connection.execute(
            sa.text(
                """
                SELECT id, code
                FROM cc_permissions
                WHERE code = ANY(:codes)
                """
            ),
            {"codes": PERMISSION_CODES},
        )
        .mappings()
        .all()
    )

    role_ids = {row["code"]: row["id"] for row in role_rows}
    permission_ids = {row["code"]: row["id"] for row in permission_rows}

    role_permission_rows = [
        {
            "role_id": role_ids[role_code],
            "permission_id": permission_ids[permission_code],
        }
        for role_code in ROLE_CODES
        for permission_code in PERMISSION_CODES
        if role_code in role_ids and permission_code in permission_ids
    ]

    if not role_permission_rows:
        return

    role_permissions_table = sa.table(
        "cc_role_permissions",
        sa.column("role_id", sa.BigInteger()),
        sa.column("permission_id", sa.BigInteger()),
    )

    op.bulk_insert(role_permissions_table, role_permission_rows)


def downgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            DELETE FROM cc_role_permissions
            WHERE role_id IN (SELECT id FROM cc_roles WHERE code = ANY(:role_codes))
              AND permission_id IN (
                  SELECT id FROM cc_permissions WHERE code = ANY(:permission_codes)
              )
            """
        ),
        {"role_codes": ROLE_CODES, "permission_codes": PERMISSION_CODES},
    )
