"""Durable job queue on top of the ``jobs`` table (DR-3).

Delivery is *at-least-once with idempotent handlers*: a job is claimed with
``FOR UPDATE SKIP LOCKED`` (so multiple workers don't grab the same one), and a
crashed worker's in-flight job is re-claimed after a visibility timeout rather
than lost. Exhausted retries land in ``FAILED`` — a dead-letter for a human.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.clock import utcnow as _now
from app.config import get_settings
from app.models import Job, JobState, JobType


def enqueue(session: Session, type_: JobType, instance_id: str | None, payload: dict | None = None) -> Job:
    job = Job(
        type=type_.value,
        instance_id=instance_id,
        payload=payload or {},
        max_attempts=get_settings().job_max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def has_active_job(session: Session, instance_id: str, type_: JobType) -> bool:
    """True if a queued/running job of this type already targets the instance —
    used by the reconciler to avoid piling up duplicate corrective work."""
    return session.scalar(
        select(Job.id).where(
            Job.instance_id == instance_id,
            Job.type == type_.value,
            Job.state.in_((JobState.QUEUED.value, JobState.RUNNING.value)),
        ).limit(1)
    ) is not None


def enqueue_once(session: Session, type_: JobType, instance_id: str, payload: dict | None = None) -> Job | None:
    if has_active_job(session, instance_id, type_):
        return None
    return enqueue(session, type_, instance_id, payload)


def claim_next(session: Session) -> Job | None:
    job = session.scalars(
        select(Job)
        .where(Job.state == JobState.QUEUED.value, Job.run_after <= _now())
        .order_by(Job.run_after)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.state = JobState.RUNNING.value
    job.claimed_at = _now()
    job.attempts += 1
    session.flush()
    return job


def complete(session: Session, job: Job) -> None:
    job.state = JobState.DONE.value
    job.error = None


def fail(session: Session, job: Job, error: str) -> None:
    if job.attempts >= job.max_attempts:
        job.state = JobState.FAILED.value  # dead-letter
        job.error = error
        return
    # Exponential backoff, capped, so a flapping dependency doesn't hot-loop.
    delay = min(60, 2 ** job.attempts)
    job.state = JobState.QUEUED.value
    job.error = error
    job.run_after = _now() + timedelta(seconds=delay)


def reclaim_stuck(session: Session) -> int:
    """Requeue jobs whose worker died mid-run (claimed longer ago than the
    visibility timeout). Returns how many were reclaimed."""
    cutoff = _now() - timedelta(seconds=get_settings().job_claim_timeout_seconds)
    stuck = session.scalars(
        select(Job).where(
            Job.state == JobState.RUNNING.value,
            or_(Job.claimed_at.is_(None), Job.claimed_at < cutoff),
        )
    ).all()
    for job in stuck:
        job.state = JobState.QUEUED.value
        job.claimed_at = None
    return len(stuck)
