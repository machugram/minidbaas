"""Test harness: a file-backed SQLite metadata DB, no scheduler, no Docker.

Environment is set *before* any app import so ``get_settings`` (lru-cached) picks
it up. Each ``client`` fixture starts from a freshly recreated schema for isolation.
"""

from __future__ import annotations

import os
import tempfile

from cryptography.fernet import Fernet

_TMP = tempfile.mkdtemp(prefix="mdbaas-test-")
os.environ.update(
    MDBAAS_DATABASE_URL=f"sqlite:///{_TMP}/test.db",
    MDBAAS_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    MDBAAS_ENABLE_SCHEDULER="false",
    MDBAAS_ENABLE_BOOTSTRAP="false",
    MDBAAS_PORT_RANGE_START="15000",
    MDBAAS_PORT_RANGE_END="15020",  # tiny pool keeps seeding fast
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _reset_schema():
    from app.db import engine, init_db
    from app.models import Base

    Base.metadata.drop_all(engine)
    init_db()


@pytest.fixture()
def db():
    """A session over a freshly recreated schema, for service-level unit tests."""
    _reset_schema()
    from app.db import SessionFactory

    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    from app.main import app

    _reset_schema()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin(client):
    """A platform-admin principal + its one-time API key + auth headers. Depends on
    ``client`` so the schema is reset before the principal is created."""
    from app.bootstrap import create_principal
    from app.db import session_scope

    with session_scope() as session:
        _, api_key = create_principal(session, "admin", is_platform_admin=True)
    return {"key": api_key, "headers": {"Authorization": f"Bearer {api_key}"}}


@pytest.fixture()
def fake_prov():
    from app.runtime import set_provisioner
    from tests.fakes import FakeProvisioner

    provisioner = FakeProvisioner()
    set_provisioner(provisioner)
    yield provisioner
    set_provisioner(None)
