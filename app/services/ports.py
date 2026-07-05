"""Atomic port allocation (DR-1 / ADR-007).

The port pool is pre-seeded as rows (db._seed_ports). Allocation claims one with
``SELECT ... FOR UPDATE SKIP LOCKED`` so two concurrent creates can never pick the
same port — the classic TOCTOU bug of "find a free port then bind it". On SQLite
(tests) the locking clause is silently omitted, which is fine single-threaded.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.errors import Conflict
from app.models import Port


def allocate_port(session: Session, instance_id: str) -> int:
    row = session.scalars(
        select(Port)
        .where(Port.instance_id.is_(None))
        .order_by(Port.port)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if row is None:
        raise Conflict("no free ports available in the configured range")
    row.instance_id = instance_id
    row.allocated_at = utcnow()
    session.flush()
    return row.port


def release_port(session: Session, instance_id: str) -> None:
    session.execute(
        update(Port)
        .where(Port.instance_id == instance_id)
        .values(instance_id=None, allocated_at=None)
    )
