"""Instance lifecycle endpoints. Mutations return quickly (202) after recording
desired state; clients poll ``/status`` for convergence (ADR-003)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session
from app.config import get_settings
from app.errors import BadRequest
from app.models import Instance, Job, Principal
from app.schemas import (
    Connection,
    InstanceCreate,
    InstanceCreatedSecret,
    InstanceOut,
    InstancePatch,
    InstanceResize,
    InstanceUpdate,
    JobOut,
)
from app.services import instances as svc
from app.services.pgops import DATABASE

router = APIRouter(prefix="/instances", tags=["instances"])


def _connection(instance: Instance) -> Connection:
    return Connection(
        host=get_settings().instance_bind_host,
        port=instance.host_port,
        database=DATABASE,
        username=instance.credential.username,
    )


@router.post("", response_model=InstanceCreatedSecret, status_code=202)
def create_instance(
    body: InstanceCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> InstanceCreatedSecret:
    instance, password = svc.create_instance(session, principal, body, idempotency_key)
    session.commit()
    # On an idempotent replay the password is not re-revealed (ADR-005); signal that
    # by returning an empty string rather than fabricating a secret.
    return InstanceCreatedSecret(
        **InstanceOut.model_validate(instance).model_dump(),
        connection=_connection(instance),
        password=password or "",
    )


@router.get("", response_model=list[InstanceOut])
def list_instances(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    team_id: str | None = Query(default=None),
    tag: str | None = Query(default=None, description="filter as key:value"),
) -> list[Instance]:
    parsed_tag = None
    if tag is not None:
        if ":" not in tag:
            raise BadRequest("tag filter must be key:value")
        key, value = tag.split(":", 1)
        parsed_tag = (key, value)
    return svc.list_instances(session, principal, team_id=team_id, tag=parsed_tag)


@router.get("/{instance_id}", response_model=InstanceOut)
def get_instance(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Instance:
    return svc.get_instance(session, principal, instance_id)


@router.patch("/{instance_id}", response_model=InstanceOut)
def update_instance(
    instance_id: str,
    body: InstanceUpdate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Instance:
    instance = svc.update_instance(session, principal, instance_id, body)
    session.commit()
    return instance


@router.post("/{instance_id}/resize", response_model=InstanceOut, status_code=202)
def resize_instance(
    instance_id: str,
    body: InstanceResize,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Instance:
    instance = svc.resize_instance(session, principal, instance_id, body)
    session.commit()
    return instance


@router.post("/{instance_id}/patch", response_model=InstanceOut, status_code=202)
def patch_instance(
    instance_id: str,
    body: InstancePatch,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> Instance:
    instance = svc.patch_instance(session, principal, instance_id, body)
    session.commit()
    return instance


@router.delete("/{instance_id}", status_code=202)
def delete_instance(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    final_backup: bool = Query(default=True),
) -> dict:
    svc.delete_instance(session, principal, instance_id, final_backup=final_backup)
    session.commit()
    return {"status": "deleting"}


@router.get("/{instance_id}/status")
def instance_status(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> dict:
    instance = svc.get_instance(session, principal, instance_id)
    return {
        "id": instance.id,
        "desired_state": instance.desired_state,
        "observed_state": instance.observed_state,
        "last_error": instance.last_error,
    }


@router.get("/{instance_id}/jobs", response_model=list[JobOut])
def instance_jobs(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> list[Job]:
    svc.get_instance(session, principal, instance_id)  # authz
    return list(
        session.scalars(
            select(Job).where(Job.instance_id == instance_id).order_by(Job.created_at.desc())
        ).all()
    )
