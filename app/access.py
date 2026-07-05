"""Team-scoped RBAC. One rank ladder so "does this principal have at least role X
on team Y" is a single comparison. Platform admins bypass team checks (BRD, and
design-review 6 notes this super-role needs its own audit treatment — it gets it
because every mutation is audited regardless of actor).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Forbidden
from app.models import Principal, Role, TeamMembership

_RANK = {Role.READONLY: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


def membership_role(session: Session, principal: Principal, team_id: str) -> Role | None:
    row = session.get(TeamMembership, (team_id, principal.id))
    return Role(row.role) if row else None


def require_role(session: Session, principal: Principal, team_id: str, minimum: Role) -> None:
    if principal.is_platform_admin:
        return
    role = membership_role(session, principal, team_id)
    if role is None:
        raise Forbidden("not a member of this team")
    if _RANK[role] < _RANK[minimum]:
        raise Forbidden(f"requires role '{minimum.value}' on this team")


def teams_for(session: Session, principal: Principal) -> list[str]:
    rows = session.scalars(
        select(TeamMembership.team_id).where(TeamMembership.principal_id == principal.id)
    ).all()
    return list(rows)
