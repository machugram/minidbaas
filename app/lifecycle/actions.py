"""Idempotent job handlers — the code that actually moves ``observed_state`` toward
``desired_state`` (ADR-003). Every handler can be retried safely because the
provisioner verbs it calls are themselves idempotent (ADR-002).
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ProvisioningError
from app.lifecycle import backups
from app.models import BackupKind, Instance, InstanceState, Job, JobType
from app.provisioner import InstanceSpec, Provisioner, size_for
from app.security import decrypt_secret
from app.services import pgops
from app.services.ports import release_port

log = logging.getLogger(__name__)


def handle_job(session: Session, provisioner: Provisioner, job: Job) -> None:
    job_type = JobType(job.type)
    instance = session.get(Instance, job.instance_id) if job.instance_id else None
    if instance is None:
        return  # instance vanished (e.g. purged) — nothing to converge

    if job_type == JobType.PROVISION:
        _provision(session, provisioner, instance)
    elif job_type == JobType.DELETE:
        _deprovision(session, provisioner, instance, job.payload)
    elif job_type == JobType.RESIZE:
        _resize(session, provisioner, instance)
    elif job_type == JobType.PATCH:
        _patch(session, provisioner, instance, job.payload)
    elif job_type == JobType.STOP:
        _stop(provisioner, instance)
    elif job_type == JobType.BACKUP:
        backups.run_backup(session, provisioner, instance, BackupKind(job.payload.get("kind", "scheduled")))


# --------------------------------------------------------------------------- #
def _spec(instance: Instance) -> InstanceSpec:
    cred = instance.credential
    return InstanceSpec(
        instance_id=instance.id,
        image=instance.image,
        size=size_for(instance.size),
        host_port=instance.host_port,
        bind_host=get_settings().instance_bind_host,
        superuser=cred.username,
        password=decrypt_secret(cred.secret_encrypted),
        team_id=instance.team_id,
    )


def _wait_ready(provisioner: Provisioner, instance: Instance, superuser: str, attempts: int = 30) -> bool:
    for _ in range(attempts):
        if pgops.pg_isready(provisioner, instance.id, superuser):
            return True
        time.sleep(1)
    return False


def _provision(session: Session, provisioner: Provisioner, instance: Instance) -> None:
    instance.observed_state = InstanceState.PROVISIONING.value
    session.flush()
    spec = _spec(instance)
    provisioned = provisioner.create(spec)
    if not _wait_ready(provisioner, instance, spec.superuser):
        raise ProvisioningError("postgres did not become ready in time")
    instance.container_id = provisioned.container_id
    instance.observed_state = InstanceState.READY.value
    instance.last_error = None


def _deprovision(session: Session, provisioner: Provisioner, instance: Instance, payload: dict) -> None:
    # Best-effort final backup (DR-7) — a backup failure must not block the delete,
    # but we record why on the instance for the audit trail.
    if payload.get("final_backup", True) and instance.observed_state in (
        InstanceState.READY.value, InstanceState.STOPPED.value,
    ):
        try:
            provisioner.start(instance.id)
            _wait_ready(provisioner, instance, instance.credential.username, attempts=15)
            backups.run_backup(session, provisioner, instance, BackupKind.PRE_DELETE)
        except Exception as exc:  # noqa: BLE001 - deletion proceeds regardless
            log.warning("final backup for %s failed: %s", instance.id, exc)
            instance.last_error = f"final backup skipped: {exc}"[:512]

    provisioner.destroy(instance.id)
    release_port(session, instance.id)
    instance.container_id = None
    instance.observed_state = InstanceState.DELETED.value


def _resize(session: Session, provisioner: Provisioner, instance: Instance) -> None:
    instance.observed_state = InstanceState.RESIZING.value
    session.flush()
    provisioner.resize(instance.id, size_for(instance.size))
    instance.observed_state = InstanceState.READY.value


def _patch(session: Session, provisioner: Provisioner, instance: Instance, payload: dict) -> None:
    instance.observed_state = InstanceState.PATCHING.value
    session.flush()
    if payload.get("pre_patch_backup", True):
        backups.run_backup(session, provisioner, instance, BackupKind.PRE_PATCH)

    # Point the spec at the new image, then swap (ADR-004: same volume, new binaries).
    instance.image = payload["image"]
    instance.pg_version = instance.image.rsplit(":", 1)[-1]
    spec = _spec(instance)
    provisioned = provisioner.patch(spec)
    if not _wait_ready(provisioner, instance, spec.superuser):
        raise ProvisioningError("postgres did not become ready after patch")
    instance.container_id = provisioned.container_id
    instance.observed_state = InstanceState.READY.value
    instance.last_error = None


def _stop(provisioner: Provisioner, instance: Instance) -> None:
    provisioner.stop(instance.id)
    instance.observed_state = InstanceState.STOPPED.value
