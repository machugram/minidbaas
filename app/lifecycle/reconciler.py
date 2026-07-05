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
from app.provisioner import Provisioner

log = logging.getLogger(__name__)


def reconcile(session: Session, provisioner: Provisioner) -> None:
    jobs.reclaim_stuck(session)

    reality = set(provisioner.list_managed())
    instances = session.scalars(
        select(Instance).where(Instance.observed_state != InstanceState.DELETED.value)
    ).all()
    known = {i.id for i in instances}

    for instance in instances:
        present = instance.id in reality
        if (
            instance.desired_state == InstanceState.READY.value
            and instance.observed_state == InstanceState.READY.value
            and not present
        ):
            # We believed it healthy but the container is gone — re-provision.
            log.info("drift: %s missing from data plane, re-provisioning", instance.id)
            jobs.enqueue_once(session, JobType.PROVISION, instance.id)
        elif instance.desired_state == InstanceState.DELETED.value and present:
            # Delete didn't fully take — ensure it finishes (no final backup on retry).
            jobs.enqueue_once(session, JobType.DELETE, instance.id, {"final_backup": False})

    # Orphans: a managed container with no live instance row is unowned — reclaim it.
    for instance_id in reality - known:
        log.warning("orphan container for %s has no live instance, destroying", instance_id)
        provisioner.destroy(instance_id)
