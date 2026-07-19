"""Drift reconciliation + orphan GC (DR-2 / ADR-003).

Crucially, this acts on ``desired_state`` — never on container presence alone — so
it can never resurrect a database the user just deleted. Ground truth for "what
actually exists" comes from the ``mdbaas.*`` labels via ``list_managed``.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lifecycle import jobs
from app.models import Instance, InstanceState, JobType
from app.provisioner import Provisioner, RuntimeStatus

log = logging.getLogger(__name__)


def reconcile(session: Session, provisioner: Provisioner) -> None:
    jobs.reclaim_stuck(session)

    reality = set(provisioner.list_managed())
    instances = session.scalars(
        select(Instance).where(Instance.observed_state != InstanceState.DELETED.value)
    ).all()
    known = {i.id for i in instances}

    for instance in instances:
        is_ready = (
            instance.desired_state == InstanceState.READY.value
            and instance.observed_state == InstanceState.READY.value
        )
        if is_ready:
            present = instance.id in reality
            # `list_managed()` deliberately counts a *stopped* container as present
            # (orphan-GC and the two-phase-expiry STOPPED state both need that), so
            # presence alone isn't proof of health — check it's actually RUNNING
            # (design-review 2.5). PROVISION's handler is already idempotent and
            # starts an existing-but-stopped container rather than erroring, so the
            # same job type covers both "missing" and "present but stopped".
            running = present and provisioner.status(instance.id) == RuntimeStatus.RUNNING
            if not running:
                reason = "missing from data plane" if not present else "present but not running"
                log.info("drift: %s %s, re-provisioning", instance.id, reason)
                jobs.enqueue_once(session, JobType.PROVISION, instance.id)
        elif instance.desired_state == InstanceState.DELETED.value and instance.id in reality:
            # Delete didn't fully take — ensure it finishes (no final backup on retry).
            jobs.enqueue_once(session, JobType.DELETE, instance.id, {"final_backup": False})

    # Orphans: a managed container with no live instance row is unowned — reclaim it.
    # Each is handled independently so one failure (e.g. a transient Docker API
    # error) can't abort the rest of the tick or roll back the drift-repair work
    # enqueued above — it just gets retried on the next reconcile pass.
    for instance_id in reality - known:
        try:
            log.warning("orphan container for %s has no live instance, destroying", instance_id)
            provisioner.destroy(instance_id)
        except Exception:
            log.exception("failed to destroy orphan container %s; will retry next reconcile", instance_id)
