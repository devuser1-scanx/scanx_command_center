"""add the users.delete permission

Deleting a user is a separate, heavier action than deactivating one, so it gets
its own permission. Only Admin holds it (Admin holds every permission).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_CODE = "users.delete"


def upgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_permissions (code, module, name, description, is_active)
            VALUES (
                :code,
                'users',
                'Delete Users',
                'Permanently delete a Command Center user account.',
                true
            )
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {"code": PERMISSION_CODE},
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM cc_roles r
            CROSS JOIN cc_permissions p
            WHERE r.code = 'admin'
              AND p.code = :code
              AND NOT EXISTS (
                  SELECT 1
                  FROM cc_role_permissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id
              )
            """
        ),
        {"code": PERMISSION_CODE},
    )


def downgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            DELETE FROM cc_role_permissions
            WHERE permission_id IN (SELECT id FROM cc_permissions WHERE code = :code)
            """
        ),
        {"code": PERMISSION_CODE},
    )
    connection.execute(
        sa.text("DELETE FROM cc_permissions WHERE code = :code"),
        {"code": PERMISSION_CODE},
    )
