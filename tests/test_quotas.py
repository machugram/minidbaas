"""Quota enforcement happens before provisioning (BRD BR-2)."""

import pytest

from app.errors import Conflict
from app.models import Instance, InstanceState, Team
from app.provisioner import size_for
from app.services import quotas


def _seed_instance(db, team, size="small"):
    inst = Instance(
        team_id=team.id, name=f"i-{size}", image="postgres:16", pg_version="16",
        size=size, host_port=15000, desired_state=InstanceState.READY.value,
        observed_state=InstanceState.READY.value, tags={},
    )
    db.add(inst)
    db.flush()


def test_instance_count_quota(db):
    team = Team(name="t", max_instances=1, max_total_memory_mb=999999, max_storage_gb=999999)
    db.add(team)
    db.flush()
    _seed_instance(db, team)
    with pytest.raises(Conflict):
        quotas.check_can_allocate(db, team, size_for("small"))


def test_memory_quota(db):
    team = Team(name="t2", max_instances=99, max_total_memory_mb=300, max_storage_gb=999999)
    db.add(team)
    db.flush()
    _seed_instance(db, team, size="small")  # 256 MB used
    with pytest.raises(Conflict):
        quotas.check_can_allocate(db, team, size_for("medium"))  # +1024 MB > 300


def test_resize_discounts_current_footprint(db):
    team = Team(name="t3", max_instances=99, max_total_memory_mb=1024, max_storage_gb=999999)
    db.add(team)
    db.flush()
    _seed_instance(db, team, size="small")
    existing = db.query(Instance).one()
    # Resizing the same instance to medium (1024) fits once its own small footprint
    # is discounted; without the discount it would breach.
    quotas.check_can_allocate(db, team, size_for("medium"), replacing=existing)
