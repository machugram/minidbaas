"""Principal (API identity) administration.

Creating a principal is otherwise only possible via ``app.bootstrap`` at process
start — this exposes the same operation over the authenticated API so a platform
admin can hand out additional keys (e.g. one per teammate) without shelling into
the metadata DB.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, bootstrap
from app.errors import Conflict, Forbidden
from app.models import Principal, Team, TeamMembership
from app.schemas import PrincipalCreate


def create_principal(session: Session, principal: Principal, req: PrincipalCreate) -> tuple[Principal, str]:
    if not principal.is_platform_admin:
        raise Forbidden("only platform admins may create principals")
    if session.scalar(select(Principal.id).where(Principal.name == req.name)):
        raise Conflict(f"a principal named '{req.name}' already exists")

    new_principal, api_key = bootstrap.create_principal(
        session, req.name, is_platform_admin=req.is_platform_admin
    )
    audit.record(session, actor_id=principal.id, action="principal.create", target=new_principal.id)
    return new_principal, api_key


def my_teams(session: Session, principal: Principal) -> list[tuple[TeamMembership, Team]]:
    """Backs the GET /me endpoint — lets a caller confirm which teams their key
    belongs to and at what role, handy when testing RBAC by hand."""
    rows = session.execute(
        select(TeamMembership, Team)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(TeamMembership.principal_id == principal.id)
    ).all()
    return [(m, t) for m, t in rows]
