"""grant front_desk all operational permissions (everything except
users/roles/clinics.assign/audit/settings, which stay admin-only)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CODES = ["front_desk"]

# Everything except the Administration-only modules (users, roles,
# clinics.assign, audit, settings). appointments.view/clinics.view/
# dashboard.view are already granted by migration 0003 and are
# idempotently skipped below if already present.
PERMISSION_CODES = [
    "appointments.update",
    "appointments.view",
    "calls.create",
    "calls.view",
    "cases.manage",
    "cases.view",
    "clinics.view",
    "dashboard.view",
    "messages.send",
    "messages.view",
    "patients.fax",
    "patients.mail",
    "patients.search",
    "patients.sms",
    "patients.view",
    "reports.manage",
    "reports.view",
    "tasks.manage",
    "tasks.view",
]


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

    existing_pairs = {
        (row["role_id"], row["permission_id"])
        for row in connection.execute(
            sa.text(
                """
                SELECT role_id, permission_id
                FROM cc_role_permissions
                WHERE role_id = ANY(:role_ids)
                """
            ),
            {"role_ids": list(role_ids.values())},
        )
        .mappings()
        .all()
    }

    role_permission_rows = [
        {
            "role_id": role_ids[role_code],
            "permission_id": permission_ids[permission_code],
        }
        for role_code in ROLE_CODES
        for permission_code in PERMISSION_CODES
        if role_code in role_ids
        and permission_code in permission_ids
        and (role_ids[role_code], permission_ids[permission_code]) not in existing_pairs
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
