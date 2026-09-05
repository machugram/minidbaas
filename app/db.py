"""Engine, session factory, and schema bootstrap.

Postgres (compose/prod) applies Alembic migrations. SQLite (tests) uses
``create_all`` so the suite stays self-contained without a migrate step.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base, Port

_settings = get_settings()

# SQLite (tests) needs check_same_thread off for the scheduler threads.
_connect_args = {"check_same_thread": False} if _settings.is_sqlite else {}
engine = create_engine(_settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

_ROOT = Path(__file__).resolve().parents[1]


def _alembic_ini() -> Path:
    """Locate alembic.ini in editable checkouts, compose/Docker (/srv), or CWD."""
    for candidate in (Path.cwd() / "alembic.ini", Path("/srv/alembic.ini"), _ROOT / "alembic.ini"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "alembic.ini not found (looked in cwd, /srv, and repo root). "
        "Run from the project root or set the working directory to the image /srv."
    )


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


def _run_alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_alembic_ini()))
    cfg.set_main_option("sqlalchemy.url", _settings.database_url)
    # Ensure script_location resolves even if CWD differs from ini dir.
    cfg.set_main_option("script_location", str(_alembic_ini().parent / "migrations"))
    command.upgrade(cfg, "head")


def _stamp_alembic_head() -> None:
    """Mark the DB as migrated when schema was created via create_all (SQLite tests)."""
    from alembic import command
    from alembic.config import Config

    try:
        ini = _alembic_ini()
    except FileNotFoundError:
        return
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", _settings.database_url)
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    command.stamp(cfg, "head")


def init_db() -> None:
    if _settings.is_sqlite:
        Base.metadata.create_all(engine)
        # Avoid re-stamping every test reset when the version table already exists.
        insp = inspect(engine)
        if "alembic_version" not in insp.get_table_names():
            _stamp_alembic_head()
    else:
        _run_alembic_upgrade()
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
