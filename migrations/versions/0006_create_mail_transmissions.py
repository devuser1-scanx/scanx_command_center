"""create mail transmissions table and patients.mail permission

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_mail_transmissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("to_addresses", sa.Text(), nullable=False),
        sa.Column("cc_addresses", sa.Text(), nullable=True),
        sa.Column("bcc_addresses", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=64), nullable=True),
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
            name="fk_cc_mail_transmissions_sent_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_mail_transmissions_appointment_id",
        "cc_mail_transmissions",
        ["appointment_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_mail_transmissions_sent_by_user_id",
        "cc_mail_transmissions",
        ["sent_by_user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_mail_transmissions_gmail_message_id",
        "cc_mail_transmissions",
        ["gmail_message_id"],
        unique=False,
    )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_permissions (code, module, name, description, is_active)
            VALUES (
                'patients.mail',
                'patients',
                'Send Patient Mail',
                'Send a patient report by email.',
                TRUE
            )
            """
        )
    )

    # Grant patients.mail to every role that already holds patients.view, so
    # mail access mirrors who can already view a patient profile.
    connection.execute(
        sa.text(
            """
            INSERT INTO cc_role_permissions (role_id, permission_id)
            SELECT rp.role_id, (SELECT id FROM cc_permissions WHERE code = 'patients.mail')
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
            WHERE permission_id IN (SELECT id FROM cc_permissions WHERE code = 'patients.mail')
            """
        )
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM cc_permissions
            WHERE code = 'patients.mail'
            """
        )
    )

    op.drop_index(
        "ix_cc_mail_transmissions_gmail_message_id",
        table_name="cc_mail_transmissions",
    )
    op.drop_index(
        "ix_cc_mail_transmissions_sent_by_user_id",
        table_name="cc_mail_transmissions",
    )
    op.drop_index(
        "ix_cc_mail_transmissions_appointment_id",
        table_name="cc_mail_transmissions",
    )
    op.drop_table("cc_mail_transmissions")
