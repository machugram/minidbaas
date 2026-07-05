"""Quota enforcement in the create path (BRD BR-2). Checked *before* anything is
provisioned, so a rejected request leaves no container behind.

Memory and CPU limits are real; ``max_storage_gb`` is a soft limit under the
default volume driver (DR-8) — we still account for it so the number is honest and
the enforcement point exists when a quota-capable storage backend lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict
from app.models import Instance, InstanceState, Team
from app.provisioner import SIZES, Size


@dataclass(frozen=True)
class Usage:
    instances: int
    memory_mb: int
    storage_gb: int


def current_usage(session: Session, team_id: str) -> Usage:
    # Everything not deleted counts (a stopped/expired instance still holds a
    # volume and a port) — conservative on purpose.
    rows = session.scalars(
        select(Instance).where(
            Instance.team_id == team_id,
            Instance.desired_state != InstanceState.DELETED.value,
        )
    ).all()
    return Usage(
        instances=len(rows),
        memory_mb=sum(SIZES[i.size].mem_mb for i in rows),
        storage_gb=sum(SIZES[i.size].storage_gb for i in rows),
    )


def check_can_allocate(session: Session, team: Team, size: Size, *, replacing: Instance | None = None) -> None:
    """Raise ``Conflict`` if adding ``size`` would breach quota. ``replacing`` lets a
    resize discount the instance's current footprint."""
    usage = current_usage(session, team.id)
    base_instances = usage.instances
    base_memory = usage.memory_mb
    base_storage = usage.storage_gb
    if replacing is not None:
        base_instances -= 1
        base_memory -= SIZES[replacing.size].mem_mb
        base_storage -= SIZES[replacing.size].storage_gb

    if base_instances + 1 > team.max_instances:
        raise Conflict(f"instance quota exceeded ({team.max_instances})")
    if base_memory + size.mem_mb > team.max_total_memory_mb:
        raise Conflict(f"memory quota exceeded ({team.max_total_memory_mb} MB)")
    if base_storage + size.storage_gb > team.max_storage_gb:
        raise Conflict(f"storage quota exceeded ({team.max_storage_gb} GB)")
