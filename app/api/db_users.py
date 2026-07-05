"""Managed database roles inside an instance."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session, provisioner
from app.models import DbUser, Principal
from app.provisioner import Provisioner
from app.schemas import DbUserCreate, DbUserSecret
from app.services import db_users as svc
from app.services.instances import get_instance

router = APIRouter(prefix="/instances/{instance_id}/users", tags=["db-users"])


@router.post("", response_model=DbUserSecret, status_code=201)
def create_db_user(
    instance_id: str,
    body: DbUserCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    prov: Provisioner = Depends(provisioner),
) -> DbUserSecret:
    username, privileges, password = svc.create_db_user(session, prov, principal, instance_id, body)
    session.commit()
    return DbUserSecret(username=username, privileges=privileges, password=password)


@router.get("", response_model=list[str])
def list_db_users(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> list[str]:
    get_instance(session, principal, instance_id)  # authz
    return list(session.scalars(select(DbUser.username).where(DbUser.instance_id == instance_id)).all())


@router.delete("/{username}", status_code=204)
def drop_db_user(
    instance_id: str,
    username: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    prov: Provisioner = Depends(provisioner),
) -> Response:
    svc.drop_db_user(session, prov, principal, instance_id, username)
    session.commit()
    return Response(status_code=204)
