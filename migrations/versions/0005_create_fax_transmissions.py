"""create fax transmissions table and patients.fax permission

Revision ID: 0005
Revises: 0004_seed_initial_admin
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004_seed_initial_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_fax_transmissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("destination_number", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("westfax_job_id", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_by_user_id", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["sent_by_user_id"],
            ["cc_users.id"],
            name="fk_cc_fax_transmissions_sent_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_fax_transmissions_appointment_id",
        "cc_fax_transmissions",
        ["appointment_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_fax_transmissions_sent_by_user_id",
        "cc_fax_transmissions",
        ["sent_by_user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_fax_transmissions_westfax_job_id",
        "cc_fax_transmissions",
        ["westfax_job_id"],
        unique=False,
    )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_permissions (code, module, name, description, is_active)
            VALUES (
                'patients.fax',
                'patients',
                'Send Patient Fax',
                'Send a patient report by fax.',
                TRUE
            )
            """
        )
    )

    # Grant patients.fax to every role that already holds patients.view, so
    # fax access mirrors who can already view a patient profile.
    connection.execute(
        sa.text(
            """
            INSERT INTO cc_role_permissions (role_id, permission_id)
            SELECT rp.role_id, (SELECT id FROM cc_permissions WHERE code = 'patients.fax')
            FROM cc_role_permissions rp
            JOIN cc_permissions p ON p.id = rp.permission_id
            WHERE p.code = 'patients.view'
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            DELETE FROM cc_role_permissions
            WHERE permission_id IN (SELECT id FROM cc_permissions WHERE code = 'patients.fax')
            """
        )
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM cc_permissions
            WHERE code = 'patients.fax'
            """
        )
    )

    op.drop_index(
        "ix_cc_fax_transmissions_westfax_job_id",
        table_name="cc_fax_transmissions",
    )
    op.drop_index(
        "ix_cc_fax_transmissions_sent_by_user_id",
        table_name="cc_fax_transmissions",
    )
    op.drop_index(
        "ix_cc_fax_transmissions_appointment_id",
        table_name="cc_fax_transmissions",
    )
    op.drop_table("cc_fax_transmissions")
