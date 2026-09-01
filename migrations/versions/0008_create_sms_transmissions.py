"""create sms transmissions table and patients.sms permission

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cc_sms_transmissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("destination_number", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("twilio_message_sid", sa.String(length=64), nullable=True),
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
            name="fk_cc_sms_transmissions_sent_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_sms_transmissions_appointment_id",
        "cc_sms_transmissions",
        ["appointment_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_sms_transmissions_sent_by_user_id",
        "cc_sms_transmissions",
        ["sent_by_user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_sms_transmissions_twilio_message_sid",
        "cc_sms_transmissions",
        ["twilio_message_sid"],
        unique=False,
    )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_permissions (code, module, name, description, is_active)
            VALUES (
                'patients.sms',
                'patients',
                'Send Patient Text Message',
                'Send a text message (SMS/RCS) to a patient.',
                TRUE
            )
            """
        )
    )

    # Grant patients.sms to every role that already holds patients.view, so
    # texting access mirrors who can already view a patient profile.
    connection.execute(
        sa.text(
            """
            INSERT INTO cc_role_permissions (role_id, permission_id)
            SELECT rp.role_id, (SELECT id FROM cc_permissions WHERE code = 'patients.sms')
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
            WHERE permission_id IN (SELECT id FROM cc_permissions WHERE code = 'patients.sms')
            """
        )
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM cc_permissions
            WHERE code = 'patients.sms'
            """
        )
    )

    op.drop_index(
        "ix_cc_sms_transmissions_twilio_message_sid",
        table_name="cc_sms_transmissions",
    )
    op.drop_index(
        "ix_cc_sms_transmissions_sent_by_user_id",
        table_name="cc_sms_transmissions",
    )
    op.drop_index(
        "ix_cc_sms_transmissions_appointment_id",
        table_name="cc_sms_transmissions",
    )
    op.drop_table("cc_sms_transmissions")
