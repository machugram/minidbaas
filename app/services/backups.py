"""Backup use-cases exposed over the API. The heavy lifting (pg_dump / restore)
lives in ``app.lifecycle.backups``; this module adds authz, state checks, and audit.
Manual backups run synchronously so the caller gets immediate feedback.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.access import require_role
from app.errors import Conflict, NotFound
from app.lifecycle import backups as backup_ops
from app.models import Backup, BackupKind, InstanceState, Principal, Role
from app.provisioner import Provisioner
from app.services.instances import get_instance


def create_manual_backup(session: Session, provisioner: Provisioner, principal: Principal, instance_id: str) -> Backup:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.MEMBER)
    if instance.observed_state != InstanceState.READY.value:
        raise Conflict("instance must be 'ready' to back up")
    backup = backup_ops.run_backup(session, provisioner, instance, BackupKind.MANUAL)
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="backup.create", target=instance_id)
    return backup


def list_backups(session: Session, principal: Principal, instance_id: str) -> list[Backup]:
    get_instance(session, principal, instance_id)  # authz (READONLY)
    return list(
        session.scalars(
            select(Backup).where(Backup.instance_id == instance_id).order_by(Backup.created_at.desc())
        ).all()
    )


def restore_backup(
    session: Session, provisioner: Provisioner, principal: Principal, instance_id: str, backup_id: str
) -> None:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.ADMIN)
    if instance.observed_state != InstanceState.READY.value:
        raise Conflict("instance must be 'ready' to restore into")
    backup = session.get(Backup, backup_id)
    if backup is None or backup.instance_id != instance_id:
        raise NotFound(f"backup '{backup_id}' not found for this instance")
    backup_ops.restore(session, provisioner, instance, backup)
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="backup.restore", target=instance_id, detail={"backup_id": backup_id})
