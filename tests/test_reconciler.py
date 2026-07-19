"""Reconciler drift detection and orphan GC (design-review 2.5 / moving-parts §F).

Covers two fixes:
1. A container that's *present but stopped* while an instance is
   desired=READY/observed=READY must be treated as drift, not silently
   reported healthy (previously only *missing* containers triggered repair).
2. A failing orphan destroy must not abort the rest of the reconcile tick or
   roll back legitimate drift-repair work enqueued earlier in the same pass.
"""

from app.lifecycle.reconciler import reconcile
from app.lifecycle.worker import drain
from app.models import Credential, Instance, InstanceState, Job, JobType, Team
from app.security import encrypt_secret


def _seed_ready_instance(db, team_id, name="db1", port=15010):
    instance = Instance(
        team_id=team_id, name=name, image="postgres:16", pg_version="16",
        size="small", host_port=port, desired_state=InstanceState.READY.value,
        observed_state=InstanceState.READY.value, tags={},
    )
    instance.credential = Credential(username="mdbaas_admin", secret_encrypted=encrypt_secret("pw"))
    db.add(instance)
    db.commit()
    return instance


def test_reconcile_restarts_a_present_but_stopped_instance(db, fake_prov):
    team = Team(name="t")
    db.add(team)
    db.flush()
    instance = _seed_ready_instance(db, team.id)

    # The container exists (present) but is stopped — this used to be invisible
    # to drift detection because list_managed() counts it as "present".
    fake_prov.instances[instance.id] = {"running": False, "image": instance.image}

    reconcile(db, fake_prov)
    db.commit()

    queued = db.query(Job).filter(
        Job.instance_id == instance.id, Job.type == JobType.PROVISION.value
    ).all()
    assert len(queued) == 1

    drain(fake_prov)
    db.refresh(instance)
    assert instance.observed_state == InstanceState.READY.value
    assert fake_prov.instances[instance.id]["running"] is True


def test_reconcile_does_not_touch_a_running_instance(db, fake_prov):
    team = Team(name="t")
    db.add(team)
    db.flush()
    instance = _seed_ready_instance(db, team.id)
    fake_prov.instances[instance.id] = {"running": True, "image": instance.image}

    reconcile(db, fake_prov)
    db.commit()

    assert db.query(Job).filter(Job.instance_id == instance.id).count() == 0


def test_orphan_destroy_failure_does_not_block_other_drift_repair(db, fake_prov):
    team = Team(name="t2")
    db.add(team)
    db.flush()
    # A legitimate instance missing from the data plane entirely (real drift).
    missing = _seed_ready_instance(db, team.id, name="missing-db", port=15011)

    # An orphan container with no instance row, whose destroy will raise once.
    fake_prov.instances["orphan-id"] = {"running": True, "image": "postgres:16"}
    fake_prov.fail_destroy_once.add("orphan-id")

    reconcile(db, fake_prov)  # must not raise
    db.commit()

    # The missing instance's repair job survived (wasn't rolled back by the
    # orphan failure) and the orphan is still there, to be retried next tick.
    assert db.query(Job).filter(
        Job.instance_id == missing.id, Job.type == JobType.PROVISION.value
    ).count() == 1
    assert "orphan-id" in fake_prov.instances

    # Next reconcile pass, with no injected failure, cleans up the orphan.
    reconcile(db, fake_prov)
    db.commit()
    assert "orphan-id" not in fake_prov.instances
