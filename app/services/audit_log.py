"""Read access to the append-only audit trail (BRD BR-5).

Named ``audit_log`` (not ``audit``) to stay distinct from ``app.audit``, the
cross-cutting writer every service calls to append a row — this module only
*queries* it, with RBAC applied: platform admins see everything, everyone else
sees only teams they belong to.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import teams_for
from app.errors import Forbidden
from app.models import AuditLog, Principal

_MAX_LIMIT = 500


def list_entries(
    session: Session, principal: Principal, *, team_id: str | None = None, limit: int = 100
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, _MAX_LIMIT))
    if principal.is_platform_admin:
        if team_id:
            stmt = stmt.where(AuditLog.team_id == team_id)
    else:
        scope = teams_for(session, principal)
        if team_id:
            if team_id not in scope:
                raise Forbidden("not a member of this team")
            stmt = stmt.where(AuditLog.team_id == team_id)
        else:
            stmt = stmt.where(AuditLog.team_id.in_(scope or ["__none__"]))
    return list(session.scalars(stmt).all())
