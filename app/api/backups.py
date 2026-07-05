"""Backup and restore endpoints. Manual backups run synchronously for immediate
feedback; scheduled backups are enqueued by the scheduler instead."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session, provisioner
from app.models import Backup, Principal
from app.provisioner import Provisioner
from app.schemas import BackupCreate, BackupOut, RestoreRequest
from app.services import backups as svc

router = APIRouter(prefix="/instances/{instance_id}", tags=["backups"])


@router.post("/backups", response_model=BackupOut, status_code=201)
def create_backup(
    instance_id: str,
    _: BackupCreate = BackupCreate(),
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    prov: Provisioner = Depends(provisioner),
) -> Backup:
    backup = svc.create_manual_backup(session, prov, principal, instance_id)
    session.commit()
    return backup


@router.get("/backups", response_model=list[BackupOut])
def list_backups(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
) -> list[Backup]:
    return svc.list_backups(session, principal, instance_id)


@router.post("/restore", status_code=202)
def restore(
    instance_id: str,
    body: RestoreRequest,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    prov: Provisioner = Depends(provisioner),
) -> dict:
    svc.restore_backup(session, prov, principal, instance_id, body.backup_id)
    session.commit()
    return {"status": "restored"}
