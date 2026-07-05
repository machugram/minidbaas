"""Team administration and membership. Creating a team is a platform-admin
action; usage and membership are visible to any member (readonly+)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session
from app.models import Principal, Team
from app.schemas import MembershipCreate, MembershipOut, QuotaUsage, TeamCreate, TeamOut
from app.services import teams as svc

router = APIRouter(prefix="/teams", tags=["teams"])


@router.post("", response_model=TeamOut, status_code=201)
def create_team(
    body: TeamCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Team:
    team = svc.create_team(session, principal, body)
    session.commit()
    return team


@router.get("", response_model=list[TeamOut])
def list_teams(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> list[Team]:
    return svc.list_teams(session, principal)


@router.get("/{team_id}", response_model=TeamOut)
def get_team(
    team_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Team:
    return svc.get_team(session, principal, team_id)


@router.get("/{team_id}/usage", response_model=QuotaUsage)
def team_usage(
    team_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> QuotaUsage:
    return svc.team_usage(session, principal, team_id)


@router.get("/{team_id}/members", response_model=list[MembershipOut])
def list_members(
    team_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> list[MembershipOut]:
    rows = svc.list_members(session, principal, team_id)
    return [MembershipOut(principal_id=p.id, principal_name=p.name, role=m.role) for m, p in rows]


@router.post("/{team_id}/members", response_model=MembershipOut, status_code=201)
def add_member(
    team_id: str,
    body: MembershipCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> MembershipOut:
    membership = svc.add_member(session, principal, team_id, body)
    target = session.get(Principal, membership.principal_id)
    session.commit()
    return MembershipOut(principal_id=target.id, principal_name=target.name, role=membership.role)


@router.delete("/{team_id}/members/{target_principal_id}", status_code=204)
def remove_member(
    team_id: str,
    target_principal_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Response:
    svc.remove_member(session, principal, team_id, target_principal_id)
    session.commit()
    return Response(status_code=204)
