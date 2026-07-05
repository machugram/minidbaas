"""Port allocation is the DR-1 hardening — verify uniqueness, reuse, exhaustion."""

import pytest

from app.errors import Conflict
from app.services.ports import allocate_port, release_port


def test_allocations_are_unique_and_reused(db):
    p1 = allocate_port(db, "i1")
    p2 = allocate_port(db, "i2")
    assert p1 != p2

    release_port(db, "i1")
    p3 = allocate_port(db, "i3")
    assert p3 == p1  # the freed (lowest) port is handed back out


def test_exhaustion_raises_conflict(db):
    # Test pool is 15000..15020 inclusive == 21 ports.
    for i in range(21):
        allocate_port(db, f"i{i}")
    with pytest.raises(Conflict):
        allocate_port(db, "overflow")
