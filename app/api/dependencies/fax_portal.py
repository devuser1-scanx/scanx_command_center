from __future__ import annotations

import ipaddress

from fastapi import Depends, HTTPException, Request, status

from app.api.dependencies.auth import require_password_change_complete
from app.models.auth import CCUser
from app.repositories.auth import collect_role_codes

# Roles that can use the Sales & FAX Referrals section today. A plain role
# check on purpose: finer-grained access (per-role toggles in Settings) comes
# later and should replace this one dependency, not every route.
FAX_PORTAL_ROLES = frozenset({"admin", "front_desk", "sales"})


def require_fax_portal_access(
    current_user: CCUser = Depends(require_password_change_complete),
) -> CCUser:
    if not set(collect_role_codes(current_user)) & FAX_PORTAL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to Sales & FAX Referrals.",
        )

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
