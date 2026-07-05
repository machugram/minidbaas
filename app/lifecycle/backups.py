"""Backups via ``pg_dump`` run *inside* the instance (ARCHITECTURE 2.4).

Running the dump in-container uses the instance's own client binaries (no
client/server version skew) and writes straight to the shared ``/backups`` volume,
so artifacts never stream through the control-plane process. RPO for the MVP is
the scheduled interval — logical dumps give no PITR (design-review 3.2), stated
honestly rather than implied otherwise.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.errors import BadRequest, ProvisioningError
from app.models import Backup, BackupKind, BackupStatus, Instance, InstanceState
from app.provisioner import Provisioner
from app.services import pgops


def _dir(instance_id: str) -> str:
    return f"/backups/{instance_id}"


def run_backup(session: Session, provisioner: Provisioner, instance: Instance, kind: BackupKind) -> Backup:
    location = f"{_dir(instance.id)}/{utcnow().strftime('%Y%m%dT%H%M%S%fZ')}.sql.gz"
    backup = Backup(instance_id=instance.id, kind=kind.value, location=location,
                    status=BackupStatus.RUNNING.value)
    session.add(backup)
    session.flush()

    superuser = instance.credential.username
    dump_cmd = (
        f"mkdir -p {_dir(instance.id)} && "
        f"pg_dump -U {superuser} -d {pgops.DATABASE} | gzip > {location}"
    )
    result = provisioner.exec(instance.id, ["sh", "-c", dump_cmd], user="postgres")
    if not result.ok:
        backup.status = BackupStatus.FAILED.value
        backup.error = result.output[:500]
        raise ProvisioningError(f"backup failed: {result.output[:200]}")

    stat = provisioner.exec(instance.id, ["sh", "-c", f"stat -c%s {location}"], user="postgres")
    backup.size_bytes = int(stat.output.strip()) if stat.ok and stat.output.strip().isdigit() else 0
    backup.status = BackupStatus.OK.value
    return backup


def restore(session: Session, provisioner: Provisioner, instance: Instance, backup: Backup) -> None:
    if backup.instance_id != instance.id:
        raise BadRequest("backup does not belong to this instance")
    superuser = instance.credential.username
    cmd = f"gunzip -c {backup.location} | psql -v ON_ERROR_STOP=1 -U {superuser} -d {pgops.DATABASE}"
    result = provisioner.exec(instance.id, ["sh", "-c", cmd], user="postgres")
    if not result.ok:
        raise ProvisioningError(f"restore failed: {result.output[:200]}")


def due_for_backup(session: Session) -> list[Instance]:
    """READY instances whose most recent successful backup is older than the RPO
    (or that have none yet)."""
    cutoff = utcnow() - timedelta(hours=get_settings().default_rpo_hours)
    instances = session.scalars(
        select(Instance).where(
            Instance.observed_state == InstanceState.READY.value,
            Instance.desired_state == InstanceState.READY.value,
        )
    ).all()
    due: list[Instance] = []
    for inst in instances:
        last_ok = session.scalar(
            select(func.max(Backup.created_at)).where(
                Backup.instance_id == inst.id, Backup.status == BackupStatus.OK.value
            )
        )
        if last_ok is None or last_ok < cutoff:
            due.append(inst)
    return due


def prune_old(session: Session, provisioner: Provisioner) -> int:
    """Delete successful backups (file + row) past the retention window. Returns
    how many were pruned."""
    cutoff = utcnow() - timedelta(days=get_settings().backup_retention_days)
    old = session.scalars(
        select(Backup).where(
            Backup.created_at < cutoff, Backup.status == BackupStatus.OK.value
        )
    ).all()
    for backup in old:
        instance = session.get(Instance, backup.instance_id)
        if instance is not None:
            provisioner.exec(instance.id, ["sh", "-c", f"rm -f {backup.location}"], user="postgres")
        session.delete(backup)
    return len(old)
