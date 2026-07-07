"""create command center authentication foundation

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLE_DATA = [
    {
        "code": "admin",
        "name": "Admin",
        "description": "Complete access to all Command Center modules and settings.",
        "is_system_role": True,
        "is_active": True,
    },
    {
        "code": "front_desk",
        "name": "Front Desk",
        "description": (
            "Restricted operational access. Exact permissions will be "
            "assigned while Front Desk modules are implemented."
        ),
        "is_system_role": True,
        "is_active": True,
    },
    {
        "code": "sonographer",
        "name": "Sonographer",
        "description": "Patient search and patient view access only.",
        "is_system_role": True,
        "is_active": True,
    },
    {
        "code": "sales",
        "name": "Sales",
        "description": "Reserved role. Permissions will be defined later.",
        "is_system_role": True,
        "is_active": True,
    },
]


PERMISSION_DATA = [
    {
        "code": "users.view",
        "module": "users",
        "name": "View Users",
        "description": "View Command Center users.",
        "is_active": True,
    },
    {
        "code": "users.create",
        "module": "users",
        "name": "Create Users",
        "description": "Create Command Center users.",
        "is_active": True,
    },
    {
        "code": "users.update",
        "module": "users",
        "name": "Update Users",
        "description": "Update Command Center users.",
        "is_active": True,
    },
    {
        "code": "users.activate",
        "module": "users",
        "name": "Activate Users",
        "description": "Activate Command Center users.",
        "is_active": True,
    },
    {
        "code": "users.deactivate",
        "module": "users",
        "name": "Deactivate Users",
        "description": "Deactivate Command Center users.",
        "is_active": True,
    },
    {
        "code": "users.assign_role",
        "module": "users",
        "name": "Assign User Roles",
        "description": "Assign roles to users.",
        "is_active": True,
    },
    {
        "code": "users.assign_clinic",
        "module": "users",
        "name": "Assign User Clinics",
        "description": "Assign clinic access to users.",
        "is_active": True,
    },
    {
        "code": "roles.view",
        "module": "roles",
        "name": "View Roles",
        "description": "View roles and role permissions.",
        "is_active": True,
    },
    {
        "code": "roles.manage",
        "module": "roles",
        "name": "Manage Roles",
        "description": "Manage roles and role permission mappings.",
        "is_active": True,
    },
    {
        "code": "clinics.view",
        "module": "clinics",
        "name": "View Clinics",
        "description": "View clinic information.",
        "is_active": True,
    },
    {
        "code": "clinics.assign",
        "module": "clinics",
        "name": "Assign Clinics",
        "description": "Assign clinics to users.",
        "is_active": True,
    },
    {
        "code": "patients.search",
        "module": "patients",
        "name": "Search Patients",
        "description": "Search for patients.",
        "is_active": True,
    },
    {
        "code": "patients.view",
        "module": "patients",
        "name": "View Patients",
        "description": "View patient details.",
        "is_active": True,
    },
    {
        "code": "dashboard.view",
        "module": "dashboard",
        "name": "View Dashboard",
        "description": "View the Command Center dashboard.",
        "is_active": True,
    },
    {
        "code": "appointments.view",
        "module": "appointments",
        "name": "View Appointments",
        "description": "View appointments.",
        "is_active": True,
    },
    {
        "code": "appointments.update",
        "module": "appointments",
        "name": "Update Appointments",
        "description": "Update appointment workflow status.",
        "is_active": True,
    },
    {
        "code": "messages.view",
        "module": "messages",
        "name": "View Messages",
        "description": "View patient message history.",
        "is_active": True,
    },
    {
        "code": "messages.send",
        "module": "messages",
        "name": "Send Messages",
        "description": "Send messages to patients.",
        "is_active": True,
    },
    {
        "code": "calls.view",
        "module": "calls",
        "name": "View Calls",
        "description": "View patient call history.",
        "is_active": True,
    },
    {
        "code": "calls.create",
        "module": "calls",
        "name": "Create Calls",
        "description": "Start patient calls.",
        "is_active": True,
    },
    {
        "code": "reports.view",
        "module": "reports",
        "name": "View Reports",
        "description": "View reports and report status.",
        "is_active": True,
    },
    {
        "code": "reports.manage",
        "module": "reports",
        "name": "Manage Reports",
        "description": "Manage report delivery and report status.",
        "is_active": True,
    },
    {
        "code": "cases.view",
        "module": "cases",
        "name": "View Cases",
        "description": "View support cases.",
        "is_active": True,
    },
    {
        "code": "cases.manage",
        "module": "cases",
        "name": "Manage Cases",
        "description": "Create, assign, update and close support cases.",
        "is_active": True,
    },
    {
        "code": "tasks.view",
        "module": "tasks",
        "name": "View Tasks",
        "description": "View staff tasks.",
        "is_active": True,
    },
    {
        "code": "tasks.manage",
        "module": "tasks",
        "name": "Manage Tasks",
        "description": "Create, assign, update and complete tasks.",
        "is_active": True,
    },
    {
        "code": "settings.view",
        "module": "settings",
        "name": "View Settings",
        "description": "View Command Center settings.",
        "is_active": True,
    },
    {
        "code": "settings.manage",
        "module": "settings",
        "name": "Manage Settings",
        "description": "Manage Command Center settings.",
        "is_active": True,
    },
    {
        "code": "audit.view",
        "module": "audit",
        "name": "View Audit Logs",
        "description": "View login and user activity audit logs.",
        "is_active": True,
    },
]


def upgrade() -> None:
    op.create_table(
        "cc_users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_email_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.BigInteger(),
            nullable=True,
        ),
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
            ["created_by_user_id"],
            ["cc_users.id"],
            name="fk_cc_users_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["cc_users.id"],
            name="fk_cc_users_updated_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_cc_users_email"),
    )

    op.create_index(
        "ix_cc_users_email",
        "cc_users",
        ["email"],
        unique=True,
    )

    op.create_table(
        "cc_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system_role",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_cc_roles_code"),
    )

    op.create_index(
        "ix_cc_roles_code",
        "cc_roles",
        ["code"],
        unique=True,
    )

    op.create_table(
        "cc_permissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code",
            name="uq_cc_permissions_code",
        ),
    )

    op.create_index(
        "ix_cc_permissions_code",
        "cc_permissions",
        ["code"],
        unique=True,
    )

    op.create_index(
        "ix_cc_permissions_module",
        "cc_permissions",
        ["module"],
        unique=False,
    )

    op.create_table(
        "cc_user_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["cc_users.id"],
            name="fk_cc_user_roles_assigned_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["cc_roles.id"],
            name="fk_cc_user_roles_role_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["cc_users.id"],
            name="fk_cc_user_roles_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "role_id",
            name="uq_cc_user_roles_user_role",
        ),
    )

    op.create_index(
        "ix_cc_user_roles_user_id",
        "cc_user_roles",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_user_roles_role_id",
        "cc_user_roles",
        ["role_id"],
        unique=False,
    )

    op.create_table(
        "cc_role_permissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("permission_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["cc_permissions.id"],
            name="fk_cc_role_permissions_permission_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["cc_roles.id"],
            name="fk_cc_role_permissions_role_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_cc_role_permissions_role_permission",
        ),
    )

    op.create_index(
        "ix_cc_role_permissions_role_id",
        "cc_role_permissions",
        ["role_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_role_permissions_permission_id",
        "cc_role_permissions",
        ["permission_id"],
        unique=False,
    )

    op.create_table(
        "cc_user_clinic_access",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("clinic_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("assigned_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["cc_users.id"],
            name="fk_cc_user_clinic_access_assigned_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["cc_users.id"],
            name="fk_cc_user_clinic_access_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "clinic_id",
            name="uq_cc_user_clinic_access_user_clinic",
        ),
    )

    op.create_index(
        "ix_cc_user_clinic_access_user_id",
        "cc_user_clinic_access",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_user_clinic_access_clinic_id",
        "cc_user_clinic_access",
        ["clinic_id"],
        unique=False,
    )

    op.create_table(
        "cc_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "refresh_token_hash",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["cc_users.id"],
            name="fk_cc_sessions_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "refresh_token_hash",
            name="uq_cc_sessions_refresh_token_hash",
        ),
    )

    op.create_index(
        "ix_cc_sessions_user_id",
        "cc_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_sessions_refresh_token_hash",
        "cc_sessions",
        ["refresh_token_hash"],
        unique=False,
    )

    op.create_index(
        "ix_cc_sessions_expires_at",
        "cc_sessions",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "cc_password_reset_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["cc_users.id"],
            name="fk_cc_password_reset_tokens_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_cc_password_reset_tokens_token_hash",
        ),
    )

    op.create_index(
        "ix_cc_password_reset_tokens_user_id",
        "cc_password_reset_tokens",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_password_reset_tokens_token_hash",
        "cc_password_reset_tokens",
        ["token_hash"],
        unique=False,
    )

    op.create_index(
        "ix_cc_password_reset_tokens_expires_at",
        "cc_password_reset_tokens",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "cc_login_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column(
            "failure_reason",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["cc_users.id"],
            name="fk_cc_login_audit_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_login_audit_user_id",
        "cc_login_audit",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_login_audit_email",
        "cc_login_audit",
        ["email"],
        unique=False,
    )

    op.create_index(
        "ix_cc_login_audit_created_at",
        "cc_login_audit",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "cc_user_activity_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column(
            "resource_type",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "resource_id",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column("clinic_id", sa.BigInteger(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["cc_users.id"],
            name="fk_cc_user_activity_audit_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["cc_users.id"],
            name="fk_cc_user_activity_audit_target_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_cc_user_activity_audit_actor_user_id",
        "cc_user_activity_audit",
        ["actor_user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_user_activity_audit_target_user_id",
        "cc_user_activity_audit",
        ["target_user_id"],
        unique=False,
    )

    op.create_index(
        "ix_cc_user_activity_audit_action",
        "cc_user_activity_audit",
        ["action"],
        unique=False,
    )

    op.create_index(
        "ix_cc_user_activity_audit_created_at",
        "cc_user_activity_audit",
        ["created_at"],
        unique=False,
    )

    roles_table = sa.table(
        "cc_roles",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_system_role", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
    )

    permissions_table = sa.table(
        "cc_permissions",
        sa.column("code", sa.String()),
        sa.column("module", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_active", sa.Boolean()),
    )

    op.bulk_insert(roles_table, ROLE_DATA)
    op.bulk_insert(permissions_table, PERMISSION_DATA)

    connection = op.get_bind()

    admin_role_id = connection.execute(
        sa.text(
            """
            SELECT id
            FROM cc_roles
            WHERE code = 'admin'
            """
        )
    ).scalar_one()

    sonographer_role_id = connection.execute(
        sa.text(
            """
            SELECT id
            FROM cc_roles
            WHERE code = 'sonographer'
            """
        )
    ).scalar_one()

    permission_rows = connection.execute(
        sa.text(
            """
            SELECT id, code
            FROM cc_permissions
            """
        )
    ).mappings()

    permission_map = {row["code"]: row["id"] for row in permission_rows}

    role_permission_rows = [
        {
            "role_id": admin_role_id,
            "permission_id": permission_id,
        }
        for permission_id in permission_map.values()
    ]

    role_permission_rows.extend(
        [
            {
                "role_id": sonographer_role_id,
                "permission_id": permission_map["patients.search"],
            },
            {
                "role_id": sonographer_role_id,
                "permission_id": permission_map["patients.view"],
            },
        ]
    )

    role_permissions_table = sa.table(
        "cc_role_permissions",
        sa.column("role_id", sa.BigInteger()),
        sa.column("permission_id", sa.BigInteger()),
    )

    op.bulk_insert(
        role_permissions_table,
        role_permission_rows,
    )

    op.drop_table("test_table")


def downgrade() -> None:
    op.create_table(
        "test_table",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.drop_index(
        "ix_cc_user_activity_audit_created_at",
        table_name="cc_user_activity_audit",
    )
    op.drop_index(
        "ix_cc_user_activity_audit_action",
        table_name="cc_user_activity_audit",
    )
    op.drop_index(
        "ix_cc_user_activity_audit_target_user_id",
        table_name="cc_user_activity_audit",
    )
    op.drop_index(
        "ix_cc_user_activity_audit_actor_user_id",
        table_name="cc_user_activity_audit",
    )
    op.drop_table("cc_user_activity_audit")

    op.drop_index(
        "ix_cc_login_audit_created_at",
        table_name="cc_login_audit",
    )
    op.drop_index(
        "ix_cc_login_audit_email",
        table_name="cc_login_audit",
    )
    op.drop_index(
        "ix_cc_login_audit_user_id",
        table_name="cc_login_audit",
    )
    op.drop_table("cc_login_audit")

    op.drop_index(
        "ix_cc_password_reset_tokens_expires_at",
        table_name="cc_password_reset_tokens",
    )
    op.drop_index(
        "ix_cc_password_reset_tokens_token_hash",
        table_name="cc_password_reset_tokens",
    )
    op.drop_index(
        "ix_cc_password_reset_tokens_user_id",
        table_name="cc_password_reset_tokens",
    )
    op.drop_table("cc_password_reset_tokens")

    op.drop_index(
        "ix_cc_sessions_expires_at",
        table_name="cc_sessions",
    )
    op.drop_index(
        "ix_cc_sessions_refresh_token_hash",
        table_name="cc_sessions",
    )
    op.drop_index(
        "ix_cc_sessions_user_id",
        table_name="cc_sessions",
    )
    op.drop_table("cc_sessions")

    op.drop_index(
        "ix_cc_user_clinic_access_clinic_id",
        table_name="cc_user_clinic_access",
    )
    op.drop_index(
        "ix_cc_user_clinic_access_user_id",
        table_name="cc_user_clinic_access",
    )
    op.drop_table("cc_user_clinic_access")

    op.drop_index(
        "ix_cc_role_permissions_permission_id",
        table_name="cc_role_permissions",
    )
    op.drop_index(
        "ix_cc_role_permissions_role_id",
        table_name="cc_role_permissions",
    )
    op.drop_table("cc_role_permissions")

    op.drop_index(
        "ix_cc_user_roles_role_id",
        table_name="cc_user_roles",
    )
    op.drop_index(
        "ix_cc_user_roles_user_id",
        table_name="cc_user_roles",
    )
    op.drop_table("cc_user_roles")

    op.drop_index(
        "ix_cc_permissions_module",
        table_name="cc_permissions",
    )
    op.drop_index(
        "ix_cc_permissions_code",
        table_name="cc_permissions",
    )
    op.drop_table("cc_permissions")

    op.drop_index(
        "ix_cc_roles_code",
        table_name="cc_roles",
    )
    op.drop_table("cc_roles")

    op.drop_index(
        "ix_cc_users_email",
        table_name="cc_users",
    )
    op.drop_table("cc_users")
