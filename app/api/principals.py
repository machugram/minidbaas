"""Principal (API identity) administration and self-lookup."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session
from app.models import Principal
from app.schemas import MeOut, MyTeam, PrincipalCreate, PrincipalCreatedSecret
from app.services import principals as svc

router = APIRouter(tags=["principals"])


@router.post("/principals", response_model=PrincipalCreatedSecret, status_code=201)
def create_principal(
    body: PrincipalCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> PrincipalCreatedSecret:
    new_principal, api_key = svc.create_principal(session, principal, body)
    session.commit()
    return PrincipalCreatedSecret(
        id=new_principal.id, name=new_principal.name,
        is_platform_admin=new_principal.is_platform_admin, api_key=api_key,
    )


@router.get("/me", response_model=MeOut)
def me(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> MeOut:
    memberships = svc.my_teams(session, principal)
    return MeOut(
        id=principal.id,
        name=principal.name,
        is_platform_admin=principal.is_platform_admin,
        teams=[MyTeam(team_id=t.id, team_name=t.name, role=m.role) for m, t in memberships],
    )
