"""Instance use-cases. Each mutation writes *desired* state + enqueues a job and
returns immediately (ADR-003); the lifecycle workers do the slow Docker work.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.access import require_role, teams_for
from app.clock import to_naive_utc
from app.config import get_settings
from app.errors import BadRequest, Conflict, NotFound
from app.models import (
    Credential,
    IdempotencyKey,
    Instance,
    InstanceState,
    Job,
    JobType,
    Principal,
    Role,
    Team,
)
from app.provisioner import size_for
from app.schemas import InstanceCreate, InstancePatch, InstanceResize, InstanceUpdate
from app.security import encrypt_secret, generate_password
from app.services import quotas
from app.services.ports import allocate_port
from app.lifecycle import jobs

_SUPERUSER = "mdbaas_admin"


def _resolve_image(image: str | None) -> tuple[str, str]:
    settings = get_settings()
    image = image or settings.pg_image_default
    if image not in settings.allowed_pg_images:
        raise BadRequest(f"image '{image}' is not allowed; choose one of {settings.allowed_pg_images}")
    return image, image.rsplit(":", 1)[-1]


def _get_team(session: Session, team_id: str) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise NotFound(f"team '{team_id}' not found")
    return team


def get_instance(session: Session, principal: Principal, instance_id: str) -> Instance:
    instance = session.get(Instance, instance_id)
    if instance is None or instance.observed_state == InstanceState.DELETED.value:
        raise NotFound(f"instance '{instance_id}' not found")
    require_role(session, principal, instance.team_id, Role.READONLY)
    return instance


def list_instances(
    session: Session, principal: Principal, *, team_id: str | None = None, tag: tuple[str, str] | None = None
) -> list[Instance]:
    stmt = select(Instance).where(Instance.observed_state != InstanceState.DELETED.value)
    if principal.is_platform_admin:
        if team_id:
            stmt = stmt.where(Instance.team_id == team_id)
    else:
        scope = teams_for(session, principal)
        stmt = stmt.where(Instance.team_id.in_(scope or ["__none__"]))
        if team_id:
            if team_id not in scope:
                return []
            stmt = stmt.where(Instance.team_id == team_id)
    rows = session.scalars(stmt.order_by(Instance.created_at.desc())).all()
    if tag:
        key, value = tag
        rows = [i for i in rows if i.tags.get(key) == value]
    return list(rows)


def create_instance(
    session: Session, principal: Principal, req: InstanceCreate, idempotency_key: str | None
) -> tuple[Instance, str | None]:
    """Returns (instance, one_time_password). Password is None when an
    Idempotency-Key replays an earlier create (the secret is only revealed once)."""
    team = _get_team(session, req.team_id)
    require_role(session, principal, team.id, Role.MEMBER)

    # DR-6: a retried create with the same key returns the original instance and
    # never provisions a second database.
    if idempotency_key:
        prior = session.get(IdempotencyKey, idempotency_key)
        if prior is not None:
            existing = session.get(Instance, prior.instance_id)
            if existing is None:
                raise Conflict("idempotency key refers to a missing instance")
            return existing, None

    image, pg_version = _resolve_image(req.image)
    size = size_for(req.size)
    quotas.check_can_allocate(session, team, size)

    if session.scalar(
        select(Instance.id).where(
            Instance.team_id == team.id,
            Instance.name == req.name,
            Instance.observed_state != InstanceState.DELETED.value,
        )
    ):
        raise Conflict(f"an instance named '{req.name}' already exists in this team")

    instance_id = uuid.uuid4().hex
    host_port = allocate_port(session, instance_id)  # DR-1: atomic claim
    password = generate_password()

    instance = Instance(
        id=instance_id,
        team_id=team.id,
        name=req.name,
        image=image,
        pg_version=pg_version,
        size=req.size,
        host_port=host_port,
        desired_state=InstanceState.READY.value,
        observed_state=InstanceState.PENDING.value,
        tags=req.tags,
        expires_at=to_naive_utc(req.expires_at),
    )
    instance.credential = Credential(
        username=_SUPERUSER, secret_encrypted=encrypt_secret(password)
    )
    session.add(instance)
    session.flush()

    jobs.enqueue(session, JobType.PROVISION, instance_id)
    if idempotency_key:
        session.add(IdempotencyKey(key=idempotency_key, principal_id=principal.id, instance_id=instance_id))
    audit.record(
        session, actor_id=principal.id, team_id=team.id, action="instance.create",
        target=instance_id, detail={"name": req.name, "size": req.size, "image": image},
    )
    return instance, password


def resize_instance(session: Session, principal: Principal, instance_id: str, req: InstanceResize) -> Instance:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.MEMBER)
    _require_ready(instance)
    new_size = size_for(req.size)
    quotas.check_can_allocate(session, instance.team, new_size, replacing=instance)
    instance.size = req.size
    jobs.enqueue(session, JobType.RESIZE, instance_id)
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="instance.resize", target=instance_id, detail={"size": req.size})
    return instance


def patch_instance(session: Session, principal: Principal, instance_id: str, req: InstancePatch) -> Instance:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.ADMIN)
    _require_ready(instance)
    image, pg_version = _resolve_image(req.image)

    # Major-version gate (ADR-004): the image swap alone can't migrate the data dir
    # across majors, so the caller must acknowledge and a pre-patch backup is forced.
    current_major = instance.pg_version.split(".")[0]
    new_major = pg_version.split(".")[0]
    pre_backup = req.pre_patch_backup
    if new_major != current_major:
        if not req.acknowledge_major_upgrade:
            raise Conflict(
                f"major upgrade {current_major}->{new_major} requires acknowledge_major_upgrade=true"
            )
        pre_backup = True

    jobs.enqueue(session, JobType.PATCH, instance_id, {"image": image, "pre_patch_backup": pre_backup})
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="instance.patch", target=instance_id, detail={"image": image})
    return instance


def update_instance(session: Session, principal: Principal, instance_id: str, req: InstanceUpdate) -> Instance:
    """Metadata-only changes (tags, TTL) — no provisioning needed."""
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.MEMBER)
    if req.tags is not None:
        instance.tags = req.tags
    if req.expires_at is not None:
        instance.expires_at = to_naive_utc(req.expires_at)
        instance.expiry_stopped_at = None  # extending TTL clears a pending expiry-stop
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="instance.update", target=instance_id)
    return instance


def delete_instance(session: Session, principal: Principal, instance_id: str, *, final_backup: bool = True) -> None:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.ADMIN)
    instance.desired_state = InstanceState.DELETED.value
    jobs.enqueue(session, JobType.DELETE, instance_id, {"final_backup": final_backup})
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="instance.delete", target=instance_id)


def retry_instance(session: Session, principal: Principal, instance_id: str) -> Instance:
    """Recover an instance stuck in FAILED.

    Exhausting a job's retries flips ``observed_state`` to FAILED (worker._STATE_CHANGING)
    but the dead-lettered job itself is never picked up again — nothing else in the
    system will move the instance forward. Without this, the only recovery was
    delete-and-recreate, even for a transient failure like a port collision.

    Re-runs whatever job type last touched the instance (PROVISION, PATCH, RESIZE,
    ...) with its original payload, so a failed patch retries the patch rather than
    silently falling back to re-provisioning.
    """
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.ADMIN)
    if instance.observed_state != InstanceState.FAILED.value:
        raise Conflict(f"instance is '{instance.observed_state}', must be 'failed' to retry")

    last_job = session.scalars(
        select(Job).where(Job.instance_id == instance_id).order_by(Job.created_at.desc()).limit(1)
    ).first()
    job_type = JobType(last_job.type) if last_job else JobType.PROVISION
    payload = last_job.payload if last_job else {}

    instance.last_error = None
    jobs.enqueue_once(session, job_type, instance_id, payload)
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="instance.retry", target=instance_id, detail={"job_type": job_type.value})
    return instance


def _require_ready(instance: Instance) -> None:
    if instance.observed_state != InstanceState.READY.value:
        raise Conflict(f"instance is '{instance.observed_state}', must be 'ready' for this operation")
