"""Two-phase TTL expiry (DR-7 / design-review 5.3).

Auto-destroy is dangerous, so expiry is deliberately gentle:

  phase 1  at ``expires_at``            -> stop the container (data intact), notify
  phase 2  after the grace window       -> final backup + destroy

The reaper only flips ``desired_state`` and enqueues jobs; the actual Docker work
is done by the worker, keeping this pass fast and transactional. Extending an
instance's TTL clears ``expiry_stopped_at`` (see services.instances.update_instance),
which cancels a pending phase-2 delete.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.clock import utcnow
from app.config import get_settings
from app.lifecycle import jobs
from app.models import Instance, InstanceState, JobType


def sweep(session: Session) -> tuple[int, int]:
    """Returns (stopped, deleted) counts for observability."""
    now = utcnow()
    grace = timedelta(hours=get_settings().expiry_grace_hours)

    stopped = session.scalars(
        select(Instance).where(
            Instance.expires_at.is_not(None),
            Instance.expires_at <= now,
            Instance.expiry_stopped_at.is_(None),
            Instance.observed_state == InstanceState.READY.value,
            Instance.desired_state == InstanceState.READY.value,
        )
    ).all()
    for instance in stopped:
        instance.desired_state = InstanceState.STOPPED.value
        instance.expiry_stopped_at = now
        jobs.enqueue_once(session, JobType.STOP, instance.id)
        audit.record(session, team_id=instance.team_id, action="instance.expire_stop", target=instance.id)

    deleted = session.scalars(
        select(Instance).where(
            Instance.expiry_stopped_at.is_not(None),
            Instance.expiry_stopped_at <= now - grace,
            Instance.desired_state != InstanceState.DELETED.value,
        )
    ).all()
    for instance in deleted:
        instance.desired_state = InstanceState.DELETED.value
        jobs.enqueue_once(session, JobType.DELETE, instance.id, {"final_backup": True})
        audit.record(session, team_id=instance.team_id, action="instance.expire_delete", target=instance.id)

    return len(stopped), len(deleted)
