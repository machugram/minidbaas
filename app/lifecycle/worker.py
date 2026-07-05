"""Worker loop: claim one job, run its handler, settle the outcome.

Claim, execute, and settle happen in *separate* transactions so that slow Docker
I/O (and the readiness sleeps in actions) don't hold the claim's row lock. A job
that crashes mid-run is re-claimed by ``jobs.reclaim_stuck`` rather than lost.
"""

from __future__ import annotations

import logging

from app.db import session_scope
from app.lifecycle import actions, jobs
from app.models import Instance, InstanceState, Job, JobType
from app.provisioner import Provisioner

log = logging.getLogger(__name__)

# Job types that own an instance's observed_state — a failure here flips the
# instance to FAILED so the reconciler/operator can see it.
_STATE_CHANGING = {JobType.PROVISION, JobType.DELETE, JobType.RESIZE, JobType.PATCH, JobType.STOP}


def process_next(provisioner: Provisioner) -> bool:
    """Process a single job. Returns False when the queue is empty."""
    with session_scope() as session:
        job = jobs.claim_next(session)
        if job is None:
            return False
        job_id = job.id

    try:
        with session_scope() as session:
            actions.handle_job(session, provisioner, session.get(Job, job_id))
        with session_scope() as session:
            jobs.complete(session, session.get(Job, job_id))
    except Exception as exc:  # noqa: BLE001 - the queue must survive any handler error
        log.exception("job %s failed", job_id)
        with session_scope() as session:
            job = session.get(Job, job_id)
            jobs.fail(session, job, str(exc)[:500])
            if job.instance_id and JobType(job.type) in _STATE_CHANGING:
                instance = session.get(Instance, job.instance_id)
                if instance and instance.observed_state != InstanceState.DELETED.value:
                    instance.observed_state = InstanceState.FAILED.value
                    instance.last_error = str(exc)[:512]
    return True


def drain(provisioner: Provisioner, max_jobs: int = 50) -> int:
    """Process up to ``max_jobs`` ready jobs; returns how many ran. The cap keeps a
    single tick bounded so maintenance ticks still get their turn."""
    processed = 0
    while processed < max_jobs and process_next(provisioner):
        processed += 1
    return processed
