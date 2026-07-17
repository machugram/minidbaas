"""Retry recovers a FAILED instance without delete+recreate.

This is the bug observed live: a port collision exhausted a job's retries,
flipped the instance to FAILED, and nothing ever moved it forward again — the
dead-lettered job is never re-claimed (jobs.claim_next only selects QUEUED), and
the reconciler only acts on desired=READY/observed=READY or desired=DELETED
(ADR-003 §DR-2), not on FAILED. A stuck instance had no path back except
deleting and recreating it.
"""

import pytest

from app.bootstrap import create_principal
from app.errors import Conflict
from app.lifecycle.worker import drain
from app.models import Credential, Instance, InstanceState, Job, JobState, JobType, Team
from app.security import encrypt_secret
from app.services.instances import retry_instance


def _seed_instance(db, team_id, *, observed_state, last_job_type=None):
    instance = Instance(
        team_id=team_id, name="stuck-db", image="postgres:16", pg_version="16",
        size="small", host_port=15005, desired_state=InstanceState.READY.value,
        observed_state=observed_state, tags={}, last_error="port conflict" if observed_state == "failed" else None,
    )
    instance.credential = Credential(username="mdbaas_admin", secret_encrypted=encrypt_secret("pw"))
    db.add(instance)
    db.flush()
    if last_job_type is not None:
        db.add(Job(instance_id=instance.id, type=last_job_type.value,
                    state=JobState.FAILED.value, attempts=5, max_attempts=5, error="port conflict"))
    db.commit()
    return instance


def test_retry_reprovisions_a_failed_instance(db, fake_prov):
    team = Team(name="t")
    db.add(team)
    db.flush()
    admin, _ = create_principal(db, "admin", is_platform_admin=True)
    db.commit()

    instance = _seed_instance(db, team.id, observed_state=InstanceState.FAILED.value,
                               last_job_type=JobType.PROVISION)

    retry_instance(db, admin, instance.id)
    db.commit()
    assert instance.last_error is None  # cleared immediately, before the job even runs

    drain(fake_prov)
    db.refresh(instance)
    assert instance.observed_state == InstanceState.READY.value


def test_retry_reruns_the_same_job_type_that_failed(db, fake_prov):
    """A failed PATCH should retry as a PATCH, not silently fall back to PROVISION."""
    team = Team(name="t")
    db.add(team)
    db.flush()
    admin, _ = create_principal(db, "admin", is_platform_admin=True)
    db.commit()

    instance = _seed_instance(db, team.id, observed_state=InstanceState.FAILED.value,
                               last_job_type=JobType.PATCH)
    fake_prov.instances[instance.id] = {"running": True, "image": instance.image}  # already provisioned

    retry_instance(db, admin, instance.id)
    db.commit()

    queued = db.query(Job).filter(Job.instance_id == instance.id, Job.state == JobState.QUEUED.value).one()
    assert queued.type == JobType.PATCH.value


def test_retry_rejects_non_failed_instance(db):
    team = Team(name="t2")
    db.add(team)
    db.flush()
    admin, _ = create_principal(db, "admin", is_platform_admin=True)
    db.commit()

    instance = _seed_instance(db, team.id, observed_state=InstanceState.READY.value)
    with pytest.raises(Conflict):
        retry_instance(db, admin, instance.id)
