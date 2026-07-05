"""Team administration: create, list, inspect, and manage membership.

Creating a team auto-enrolls the creator as its ``OWNER`` (BRD BR-1: every
resource is owned by a team, and a team must always have someone able to
administer it — otherwise a platform admin would be the only path back in).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.access import require_role, teams_for
from app.errors import Conflict, Forbidden, NotFound
from app.models import Principal, Role, Team, TeamMembership
from app.schemas import MembershipCreate, QuotaUsage, TeamCreate
from app.services import quotas


def create_team(session: Session, principal: Principal, req: TeamCreate) -> Team:
    if not principal.is_platform_admin:
        raise Forbidden("only platform admins may create teams")
    if session.scalar(select(Team.id).where(Team.name == req.name)):
        raise Conflict(f"a team named '{req.name}' already exists")

    team = Team(**req.model_dump())
    session.add(team)
    session.flush()
    session.add(TeamMembership(team_id=team.id, principal_id=principal.id, role=Role.OWNER.value))
    audit.record(session, actor_id=principal.id, team_id=team.id, action="team.create", target=team.id)
    return team


def get_team(session: Session, principal: Principal, team_id: str) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise NotFound(f"team '{team_id}' not found")
    require_role(session, principal, team_id, Role.READONLY)
    return team


def list_teams(session: Session, principal: Principal) -> list[Team]:
    if principal.is_platform_admin:
        return list(session.scalars(select(Team).order_by(Team.created_at)).all())
    scope = teams_for(session, principal)
    if not scope:
        return []
    return list(session.scalars(select(Team).where(Team.id.in_(scope)).order_by(Team.created_at)).all())


def team_usage(session: Session, principal: Principal, team_id: str) -> QuotaUsage:
    get_team(session, principal, team_id)  # existence + authz
    usage = quotas.current_usage(session, team_id)
    return QuotaUsage(instances=usage.instances, memory_mb=usage.memory_mb, storage_gb=usage.storage_gb)


def list_members(session: Session, principal: Principal, team_id: str) -> list[tuple[TeamMembership, Principal]]:
    get_team(session, principal, team_id)  # authz
    rows = session.execute(
        select(TeamMembership, Principal)
        .join(Principal, Principal.id == TeamMembership.principal_id)
        .where(TeamMembership.team_id == team_id)
    ).all()
    return [(m, p) for m, p in rows]


def add_member(session: Session, principal: Principal, team_id: str, req: MembershipCreate) -> TeamMembership:
    get_team(session, principal, team_id)  # existence
    require_role(session, principal, team_id, Role.ADMIN)
    target = session.get(Principal, req.principal_id)
    if target is None:
        raise NotFound(f"principal '{req.principal_id}' not found")
    if session.get(TeamMembership, (team_id, target.id)) is not None:
        raise Conflict(f"'{target.name}' is already a member of this team")

    membership = TeamMembership(team_id=team_id, principal_id=target.id, role=req.role)
    session.add(membership)
    audit.record(session, actor_id=principal.id, team_id=team_id, action="team.add_member",
                 target=target.id, detail={"role": req.role})
    return membership


def remove_member(session: Session, principal: Principal, team_id: str, target_principal_id: str) -> None:
    get_team(session, principal, team_id)  # existence
    require_role(session, principal, team_id, Role.ADMIN)
    membership = session.get(TeamMembership, (team_id, target_principal_id))
    if membership is None:
        raise NotFound("membership not found")
    session.delete(membership)
    audit.record(session, actor_id=principal.id, team_id=team_id, action="team.remove_member",
                 target=target_principal_id)
