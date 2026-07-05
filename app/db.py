"""Engine, session factory, and dev-time schema bootstrap.

Production uses Alembic migrations; ``init_db`` (create_all + port seeding) is a
convenience for local/dev and the test-suite so ``docker-compose up`` yields a
working system without a migrate step.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base, Port

_settings = get_settings()

# SQLite (tests) needs check_same_thread off for the scheduler threads.
_connect_args = {"check_same_thread": False} if _settings.is_sqlite else {}
engine = create_engine(_settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers. Commits on success, rolls back on error."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency. The request handler owns the commit boundary."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(engine)
    _seed_ports()


def _seed_ports() -> None:
    """Materialise the port range as rows so allocation is an atomic UPDATE (DR-1).

    Idempotent: only inserts ports that are missing, so widening the range later
    is safe.
    """
    with session_scope() as session:
        existing = {p for (p,) in session.query(Port.port).all()}
        wanted = range(_settings.port_range_start, _settings.port_range_end + 1)
        session.add_all(Port(port=p) for p in wanted if p not in existing)
