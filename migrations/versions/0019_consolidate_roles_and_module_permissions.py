"""consolidate roles around the seven module permissions

* Front Desk is merged into Sales Agent: every Front Desk user keeps their
  account and is given the Sales role, then the Front Desk role is removed.
* Adds the fax_portal.access permission, so the FAX Portal is gated by a
  permission like every other module instead of a hard-coded role list.
* Sets Sales Agent and Sonographer to exactly the finalised module matrix and
  makes sure Admin holds every permission.

Module -> permission codes:

    1 Dashboard      dashboard.view, clinics.view
    2 Messages       messages.view, messages.send
    3 Calls          calls.view, calls.create
    4 Reports        reports.view, reports.manage
    5 Patients       patients.search/view/fax/mail/sms, appointments.view/update,
                     clinics.view
    6 FAX Portal     fax_portal.access
    7 Administration users.*, roles.*, clinics.assign, audit.view, settings.*
                     (Admin only)

Role -> modules:

    Sales Agent   1 2 3 4 5 6
    Sonographer   1 2 3 4 5
    Admin         all

The cases.* / tasks.* permissions belong to the cancelled Cases & Tasks
module: no module grants them to Sales Agent or Sonographer any more (Admin
keeps them through "all"). The Appointments page is cancelled too, but
appointments.view/update still guard the appointment-status action on a
patient, so they stay inside Patients.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FAX_PORTAL_PERMISSION = "fax_portal.access"

MODULE_PERMISSIONS: dict[str, list[str]] = {
    "dashboard": ["dashboard.view", "clinics.view"],
    "messages": ["messages.view", "messages.send"],
    "calls": ["calls.view", "calls.create"],
    "reports": ["reports.view", "reports.manage"],
    "patients": [
        "patients.search",
        "patients.view",
        "patients.fax",
        "patients.mail",
        "patients.sms",
        "appointments.view",
        "appointments.update",
        "clinics.view",
    ],
    "fax_portal": [FAX_PORTAL_PERMISSION],
}

ROLE_MODULES: dict[str, list[str]] = {
    "sales": ["dashboard", "messages", "calls", "reports", "patients", "fax_portal"],
    "sonographer": ["dashboard", "messages", "calls", "reports", "patients"],
}

SALES_ROLE_NAME = "Sales Agent"
SALES_ROLE_DESCRIPTION = (
    "Dashboard, Messages, Calls, Reports, Patients and FAX Portal. "
    "Includes everything the former Front Desk role could do."
)
SONOGRAPHER_ROLE_DESCRIPTION = "Dashboard, Messages, Calls, Reports and Patients."

# --- values restored by downgrade() -----------------------------------

OLD_SALES_ROLE_NAME = "Sales"
OLD_SALES_ROLE_DESCRIPTION = "Reserved role. Permissions will be defined later."
OLD_SONOGRAPHER_ROLE_DESCRIPTION = "Patient search and patient view access only."
OLD_SONOGRAPHER_PERMISSIONS = [
    "patients.search",
    "patients.view",
    "dashboard.view",
    "appointments.view",
    "clinics.view",
]
FRONT_DESK_DESCRIPTION = (
    "Restricted operational access. Exact permissions will be "
    "assigned while Front Desk modules are implemented."
)
# What migration 0010 gave Front Desk.
OLD_FRONT_DESK_PERMISSIONS = [
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


def _role_id(connection: sa.Connection, code: str) -> int | None:
    return connection.execute(
        sa.text("SELECT id FROM cc_roles WHERE code = :code"),
        {"code": code},
    ).scalar_one_or_none()


def _set_role_permissions(
    connection: sa.Connection,
    role_id: int,
    permission_codes: list[str],
) -> None:
    """Makes the role hold exactly these permissions."""
    connection.execute(
        sa.text("DELETE FROM cc_role_permissions WHERE role_id = :role_id"),
        {"role_id": role_id},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO cc_role_permissions (role_id, permission_id)
            SELECT :role_id, id
            FROM cc_permissions
            WHERE code = ANY(:codes)
            """
        ),
        {"role_id": role_id, "codes": permission_codes},
    )


def upgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO cc_permissions (code, module, name, description, is_active)
            VALUES (
                :code,
                'fax_portal',
                'Access FAX Portal',
                'Use the FAX Portal (Sales & FAX Referrals).',
                true
            )
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {"code": FAX_PORTAL_PERMISSION},
    )

    sales_id = _role_id(connection, "sales")
    sonographer_id = _role_id(connection, "sonographer")
    front_desk_id = _role_id(connection, "front_desk")
    admin_id = _role_id(connection, "admin")

    if sales_id is None:
        raise RuntimeError("The 'sales' role is missing; cannot merge Front Desk into it.")

    connection.execute(
        sa.text(
            "UPDATE cc_roles SET name = :name, description = :description, "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": sales_id, "name": SALES_ROLE_NAME, "description": SALES_ROLE_DESCRIPTION},
    )

    # Front Desk users stay exactly as they are (cc_users is untouched); only
    # their role changes. A user who somehow holds both roles keeps one Sales
    # row (uq_cc_user_roles_user_role).
    if front_desk_id is not None:
        connection.execute(
            sa.text(
                """
                INSERT INTO cc_user_roles (user_id, role_id, assigned_by_user_id, assigned_at)
                SELECT user_id, :sales_id, assigned_by_user_id, assigned_at
                FROM cc_user_roles
                WHERE role_id = :front_desk_id
                ON CONFLICT (user_id, role_id) DO NOTHING
                """
            ),
            {"sales_id": sales_id, "front_desk_id": front_desk_id},
        )
        connection.execute(
            sa.text("DELETE FROM cc_user_roles WHERE role_id = :id"),
            {"id": front_desk_id},
        )
        # cc_role_permissions rows go with it (ON DELETE CASCADE).
        connection.execute(
            sa.text("DELETE FROM cc_roles WHERE id = :id"),
            {"id": front_desk_id},
        )

    for role_id, role_code in ((sales_id, "sales"), (sonographer_id, "sonographer")):
        if role_id is None:
            continue

        codes = sorted(
            {code for module in ROLE_MODULES[role_code] for code in MODULE_PERMISSIONS[module]}
        )
        _set_role_permissions(connection, role_id, codes)

    if sonographer_id is not None:
        connection.execute(
            sa.text(
                "UPDATE cc_roles SET description = :description, updated_at = now() WHERE id = :id"
            ),
            {"id": sonographer_id, "description": SONOGRAPHER_ROLE_DESCRIPTION},
        )

    # Admin holds every permission, including fax_portal.access just added.
    if admin_id is not None:
        connection.execute(
            sa.text(
                """
                INSERT INTO cc_role_permissions (role_id, permission_id)
                SELECT :admin_id, p.id
                FROM cc_permissions p
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM cc_role_permissions rp
                    WHERE rp.role_id = :admin_id AND rp.permission_id = p.id
                )
                """
            ),
            {"admin_id": admin_id},
        )


def downgrade() -> None:
    """Restores the roles and permissions as they were before 0019.

    Front Desk users are NOT moved back: after the upgrade nothing records
    which Sales Agents used to be Front Desk, so they stay Sales Agents. The
    Front Desk role is recreated empty of users and can be reassigned by hand.
    """
    connection = op.get_bind()

    sales_id = _role_id(connection, "sales")
    sonographer_id = _role_id(connection, "sonographer")

    if _role_id(connection, "front_desk") is None:
        connection.execute(
            sa.text(
                """
                INSERT INTO cc_roles (code, name, description, is_system_role, is_active)
                VALUES ('front_desk', 'Front Desk', :description, true, true)
                """
            ),
            {"description": FRONT_DESK_DESCRIPTION},
        )

    front_desk_id = _role_id(connection, "front_desk")
    _set_role_permissions(connection, front_desk_id, OLD_FRONT_DESK_PERMISSIONS)

    if sales_id is not None:
        connection.execute(
            sa.text(
                "UPDATE cc_roles SET name = :name, description = :description, "
                "updated_at = now() WHERE id = :id"
            ),
            {
                "id": sales_id,
                "name": OLD_SALES_ROLE_NAME,
                "description": OLD_SALES_ROLE_DESCRIPTION,
            },
        )
        _set_role_permissions(connection, sales_id, [])

    if sonographer_id is not None:
        connection.execute(
            sa.text(
                "UPDATE cc_roles SET description = :description, updated_at = now() WHERE id = :id"
            ),
            {"id": sonographer_id, "description": OLD_SONOGRAPHER_ROLE_DESCRIPTION},
        )
        _set_role_permissions(connection, sonographer_id, OLD_SONOGRAPHER_PERMISSIONS)

    # cc_role_permissions rows go with it (ON DELETE CASCADE).
    connection.execute(
        sa.text("DELETE FROM cc_permissions WHERE code = :code"),
        {"code": FAX_PORTAL_PERMISSION},
    )
