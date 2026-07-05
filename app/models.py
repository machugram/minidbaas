"""Metadata schema — the control plane's source of truth (ARCHITECTURE 2.3).

Design notes worth knowing while reading:

* Primary keys are 32-char hex UUIDs stored as strings, so the schema is portable
  across Postgres (prod) and SQLite (tests) without server-side UUID types.
* Every ``Instance`` carries both a ``desired_state`` and an ``observed_state``.
  Handlers only write ``desired_state``; the reconciler drives observed toward it
  (ADR-003). This split is why the two never collapse into one "status" column.
* Enums are stored as plain strings and validated in Python — again for backend
  portability and painless migrations.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from app.clock import utcnow as _now
from sqlalchemy import (
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class Role(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    READONLY = "readonly"


class InstanceState(str, enum.Enum):
    PENDING = "pending"          # desired READY, not yet realised
    PROVISIONING = "provisioning"
    READY = "ready"
    PATCHING = "patching"
    RESIZING = "resizing"
    BACKING_UP = "backing_up"
    STOPPED = "stopped"          # phase 1 of expiry (DR-7); data intact
    FAILED = "failed"            # reconciler-recoverable
    DELETED = "deleted"          # terminal


class JobType(str, enum.Enum):
    PROVISION = "provision"
    DELETE = "delete"
    RESIZE = "resize"
    PATCH = "patch"
    BACKUP = "backup"
    STOP = "stop"


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"            # exhausted retries → dead-letter for a human


class BackupKind(str, enum.Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    PRE_PATCH = "pre_patch"
    PRE_DELETE = "pre_delete"


class BackupStatus(str, enum.Enum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


# --------------------------------------------------------------------------- #
# Tenancy & identity
# --------------------------------------------------------------------------- #
class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    # Quotas enforced in the create path (services/quotas.py). Storage is a *soft*
    # limit under the default Docker volume driver — see design-review 4.1 / DR-8.
    max_instances: Mapped[int] = mapped_column(Integer, default=10)
    max_total_memory_mb: Mapped[int] = mapped_column(Integer, default=8192)
    max_storage_gb: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    instances: Mapped[list["Instance"]] = relationship(back_populates="team")


class Principal(Base):
    """An API identity. The API key is stored only as a bcrypt hash (DR-5)."""

    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(128))
    is_platform_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class TeamMembership(Base):
    __tablename__ = "team_memberships"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default=Role.MEMBER.value)


# --------------------------------------------------------------------------- #
# Instances & the things attached to them
# --------------------------------------------------------------------------- #
class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(16), default="postgres")
    image: Mapped[str] = mapped_column(String(128))
    pg_version: Mapped[str] = mapped_column(String(16))
    size: Mapped[str] = mapped_column(String(16))
    host_port: Mapped[int] = mapped_column(Integer)

    desired_state: Mapped[str] = mapped_column(String(16), default=InstanceState.READY.value)
    observed_state: Mapped[str] = mapped_column(String(16), default=InstanceState.PENDING.value)

    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expiry_stopped_at: Mapped[datetime | None] = mapped_column(nullable=True)  # DR-7 phase 1
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    team: Mapped[Team] = relationship(back_populates="instances")
    credential: Mapped["Credential"] = relationship(
        back_populates="instance", uselist=False, cascade="all, delete-orphan"
    )
    backups: Mapped[list["Backup"]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_instance_team_name"),)


class Credential(Base):
    """Encrypted superuser secret. Plaintext is revealed once at create and never stored."""

    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"), unique=True)
    username: Mapped[str] = mapped_column(String(64), default="mdbaas_admin")
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    rotated_at: Mapped[datetime] = mapped_column(default=_now)

    instance: Mapped[Instance] = relationship(back_populates="credential")


class DbUser(Base):
    """A managed role *inside* an instance — distinct from a Principal (ARCHITECTURE 2.3)."""

    __tablename__ = "db_users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    privileges: Mapped[str] = mapped_column(String(32), default="readwrite")
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (UniqueConstraint("instance_id", "username", name="uq_dbuser_instance_name"),)


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    location: Mapped[str] = mapped_column(String(256))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=BackupStatus.RUNNING.value)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Job(Base):
    """Durable work item = the async queue (ARCHITECTURE 2.3, DR-3).

    Claimed with ``FOR UPDATE SKIP LOCKED`` and processed by idempotent handlers,
    so a crashed worker's job is re-claimed rather than lost or double-run.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("instances.id"), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default=JobState.QUEUED.value, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(default=_now, index=True)  # backoff gate
    claimed_at: Mapped[datetime | None] = mapped_column(nullable=True)     # visibility timeout
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class Port(Base):
    """Pre-materialised port pool. Allocation is an atomic row claim (DR-1)."""

    __tablename__ = "ports"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    allocated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class IdempotencyKey(Base):
    """Maps a client-supplied Idempotency-Key to the instance it created (DR-6)."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(32))
    instance_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AuditLog(Base):
    """Append-only record of every mutating action (BRD BR-5)."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(default=_now)
