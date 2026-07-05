"""Team administration. Creating teams is a platform-admin action; usage is
visible to any member."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.access import require_role
from app.api.deps import current_principal, db_session
from app.errors import Forbidden, NotFound
from app.models import Instance, InstanceState, Principal, Role, Team
from app.schemas import QuotaUsage, TeamCreate, TeamOut
from app.services import quotas

router = APIRouter(prefix="/teams", tags=["teams"])


@router.post("", response_model=TeamOut, status_code=201)
def create_team(
    body: TeamCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Team:
    if not principal.is_platform_admin:
        raise Forbidden("only platform admins may create teams")
    team = Team(**body.model_dump())
    session.add(team)
    session.commit()
    return team


@router.get("/{team_id}/usage", response_model=QuotaUsage)
def team_usage(
    team_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> QuotaUsage:
    team = session.get(Team, team_id)
    if team is None:
        raise NotFound(f"team '{team_id}' not found")
    require_role(session, principal, team_id, Role.READONLY)
    usage = quotas.current_usage(session, team_id)
    return QuotaUsage(instances=usage.instances, memory_mb=usage.memory_mb, storage_gb=usage.storage_gb)
