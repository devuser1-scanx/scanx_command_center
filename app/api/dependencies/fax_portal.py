from __future__ import annotations

import ipaddress

from fastapi import Depends, Request

from app.api.dependencies.auth import require_permission
from app.models.auth import CCUser

# The "FAX Portal" module permission (Sales & FAX Referrals). Granted to roles
# in cc_role_permissions like any other permission - Admin and Sales Agent hold
# it today (migration 0019) - so changing who sees the section is a data
# change, not a code change.
FAX_PORTAL_PERMISSION = "fax_portal.access"


def require_fax_portal_access(
    current_user: CCUser = Depends(require_permission(FAX_PORTAL_PERMISSION)),
) -> CCUser:
    return current_user


def request_ip(request: Request) -> str | None:
    """Client IP for the audit log: first X-Forwarded-For hop (Cloud Run sits
    behind Google's proxy), else the socket peer. None if not a valid IP."""
    forwarded = request.headers.get("x-forwarded-for", "")
    candidate = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")

    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None
