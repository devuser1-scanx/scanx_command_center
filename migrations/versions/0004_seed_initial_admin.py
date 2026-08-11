"""Seed the initial administrator account.

Revision ID: 0004_seed_initial_admin
Revises: 0003
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

from app.core.security import hash_password

# Revision identifiers, used by Alembic.
revision = "0004_seed_initial_admin"
down_revision = "0003"
branch_labels = None
depends_on = None

def upgrade() -> None:
    connection = op.get_bind()

    email = "bhavin@scanx.care"

    admin_role_id = connection.execute(
        sa.text(
            """
            SELECT id
            FROM cc_roles
            WHERE code = 'admin'
            """
        )
    ).scalar_one_or_none()

    if admin_role_id is None:
        raise RuntimeError("Admin role does not exist.")

    user_id = connection.execute(
        sa.text(
            """
            SELECT id
            FROM cc_users
            WHERE LOWER(email) = LOWER(:email)
            """
        ),
        {"email": email},
    ).scalar_one_or_none()

    if user_id is None:
        password = os.getenv("INITIAL_ADMIN_PASSWORD")

        if not password:
            raise RuntimeError(
                "INITIAL_ADMIN_PASSWORD is required to seed the initial admin."
            )

        user_id = connection.execute(
            sa.text(
                """
                INSERT INTO cc_users (
                    email,
                    first_name,
                    last_name,
                    phone,
                    password_hash,
                    is_active,
                    is_email_verified,
                    must_change_password,
                    failed_login_attempts
                )
                VALUES (
                    :email,
                    :first_name,
                    :last_name,
                    NULL,
                    :password_hash,
                    TRUE,
                    TRUE,
                    TRUE,
                    0
                )
                RETURNING id
                """
            ),
            {
                "email": email,
                "first_name": "Bhavin",
                "last_name": "ScanX",
                "password_hash": hash_password(
                    password
                ),
            },
        ).scalar_one()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_user_roles (
                user_id,
                role_id,
                assigned_by_user_id
            )
            SELECT
                :user_id,
                :role_id,
                NULL
            WHERE NOT EXISTS (
                SELECT 1
                FROM cc_user_roles
                WHERE user_id = :user_id
                  AND role_id = :role_id
            )
            """
        ),
        {
            "user_id": user_id,
            "role_id": admin_role_id,
        },
    )


def downgrade() -> None:
    # Intentionally preserve the administrative account.
    pass