"""API request/response models. Kept separate from ORM models so the wire
contract can evolve independently of the schema, and so secrets never leak into a
response by accident — passwords appear only in the two schemas that end in
``Secret``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Size = Literal["small", "medium", "large"]


# --- Teams ----------------------------------------------------------------- #
class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    max_instances: int = 10
    max_total_memory_mb: int = 8192
    max_storage_gb: int = 100


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    max_instances: int
    max_total_memory_mb: int
    max_storage_gb: int


class QuotaUsage(BaseModel):
    instances: int
    memory_mb: int
    storage_gb: int


TeamRole = Literal["owner", "admin", "member", "readonly"]


class MembershipCreate(BaseModel):
    principal_id: str
    role: TeamRole = "member"


class MembershipOut(BaseModel):
    principal_id: str
    principal_name: str
    role: str


# --- Principals (API identities) -------------------------------------------- #
class PrincipalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    is_platform_admin: bool = False


class PrincipalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    is_platform_admin: bool


class PrincipalCreatedSecret(PrincipalOut):
    """Returned once, at create time — carries the bearer API key (mirrors the
    one-time-reveal pattern used for instance credentials, ADR-005)."""

    api_key: str


class MyTeam(BaseModel):
    team_id: str
    team_name: str
    role: str


class MeOut(BaseModel):
    id: str
    name: str
    is_platform_admin: bool
    teams: list[MyTeam]


# --- Audit ------------------------------------------------------------------ #
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    actor_id: str | None
    team_id: str | None
    action: str
    target: str
    detail: dict | None
    at: datetime


# --- Instances ------------------------------------------------------------- #
class InstanceCreate(BaseModel):
    team_id: str
    name: str = Field(min_length=1, max_length=128)
    size: Size = "small"
    # Optional explicit image; defaults to the configured Postgres image. Must be
    # on the allow-list (validated in the service, not here).
    image: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None


class InstanceResize(BaseModel):
    size: Size


class InstancePatch(BaseModel):
    image: str
    pre_patch_backup: bool = True
    # Major-version bumps are gated: the image swap alone would fail to start on an
    # incompatible data dir, so the caller must acknowledge (ADR-004).
    acknowledge_major_upgrade: bool = False


class InstanceUpdate(BaseModel):
    tags: dict[str, str] | None = None
    expires_at: datetime | None = None


class Connection(BaseModel):
    host: str
    port: int
    database: str
    username: str


class InstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    team_id: str
    name: str
    engine: str
    image: str
    pg_version: str
    size: str
    desired_state: str
    observed_state: str
    tags: dict
    expires_at: datetime | None
    created_at: datetime


class InstanceCreatedSecret(InstanceOut):
    """Returned once, at create time — carries the superuser password (ADR-005)."""

    connection: Connection
    password: str


# --- Credentials ----------------------------------------------------------- #
class CredentialRotatedSecret(BaseModel):
    username: str
    password: str
    rotated_at: datetime


# --- Managed DB users ------------------------------------------------------ #
class DbUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    privileges: Literal["readonly", "readwrite"] = "readwrite"


class DbUserSecret(BaseModel):
    username: str
    privileges: str
    password: str


# --- Backups --------------------------------------------------------------- #
class BackupCreate(BaseModel):
    kind: Literal["manual"] = "manual"


class BackupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    instance_id: str
    kind: str
    location: str
    size_bytes: int
    status: str
    created_at: datetime


class RestoreRequest(BaseModel):
    backup_id: str


# --- Jobs ------------------------------------------------------------------ #
class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    instance_id: str | None
    type: str
    state: str
    attempts: int
    error: str | None
