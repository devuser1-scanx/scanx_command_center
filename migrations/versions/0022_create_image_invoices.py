"""create image invoices table and patients.image_invoice permission

The "Send Image Invoice" button on a patient profile emails the patient a fixed
$45 Stripe invoice for their ultrasound exam images. This is separate from the
referral-organization invoicing in the production database.

* cc_image_invoices: one row per send attempt.
* A partial unique index allows only one pending/sent/paid invoice per
  appointment, so two simultaneous clicks cannot bill a patient twice.
* patients.image_invoice permission, granted to every role that already holds
  patients.view (so: Admin, Sales Agent and Sonographer).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_CODE = "patients.image_invoice"


def upgrade() -> None:
    op.create_table(
        "cc_image_invoices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("customer_email", sa.String(length=255), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=100), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(length=100), nullable=True),
        sa.Column("hosted_invoice_url", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_stripe_event_id", sa.String(length=150), nullable=True),
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
            name="fk_cc_image_invoices_sent_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_image_invoices_appointment_id",
        "cc_image_invoices",
        ["appointment_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_image_invoices_stripe_invoice_id",
        "cc_image_invoices",
        ["stripe_invoice_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_image_invoices_sent_by_user_id",
        "cc_image_invoices",
        ["sent_by_user_id"],
        unique=False,
    )

    op.create_index(
        "uq_cc_image_invoices_active_appointment",
        "cc_image_invoices",
        ["appointment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'sent', 'paid')"),
    )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_permissions (code, module, name, description, is_active)
            VALUES (
                :code,
                'patients',
                'Send Ultrasound Image Invoice',
                'Email a patient a Stripe invoice for their ultrasound exam images.',
                TRUE
            )
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {"code": PERMISSION_CODE},
    )

    # Grant patients.image_invoice to every role that already holds
    # patients.view, so access mirrors who can already open a patient profile.
    connection.execute(
        sa.text(
            """
            INSERT INTO cc_role_permissions (role_id, permission_id)
            SELECT DISTINCT rp.role_id, (SELECT id FROM cc_permissions WHERE code = :code)
            FROM cc_role_permissions rp
            JOIN cc_permissions p ON p.id = rp.permission_id
            WHERE p.code = 'patients.view'
              AND NOT EXISTS (
                  SELECT 1
                  FROM cc_role_permissions existing
                  WHERE existing.role_id = rp.role_id
                    AND existing.permission_id = (
                        SELECT id FROM cc_permissions WHERE code = :code
                    )
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

    op.drop_index(
        "uq_cc_image_invoices_active_appointment",
        table_name="cc_image_invoices",
    )
    op.drop_index(
        "ix_cc_image_invoices_sent_by_user_id",
        table_name="cc_image_invoices",
    )
    op.drop_index(
        "ix_cc_image_invoices_stripe_invoice_id",
        table_name="cc_image_invoices",
    )
    op.drop_index(
        "ix_cc_image_invoices_appointment_id",
        table_name="cc_image_invoices",
    )
    op.drop_table("cc_image_invoices")
