"""The provisioner contract (ADR-002).

Everything is expressed in domain terms — an ``InstanceSpec``, a ``Size``, a
``RuntimeStatus`` — never in Docker terms. That is what lets a future
``KubernetesProvisioner`` satisfy the same ``Protocol`` without any caller change.
Instances are addressed by their stable ``instance_id``; the driver derives its
own resource names from it, so callers never hold container ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from app.errors import BadRequest


@dataclass(frozen=True)
class Size:
    name: str
    mem_mb: int
    nano_cpus: int          # Docker CPU quota: 1.0 CPU == 1_000_000_000
    storage_gb: int         # advisory only under the default volume driver (DR-8)


# T-shirt sizes → concrete, enforceable resource limits (memory + CPU are real;
# storage is soft — see design-review 4.1).
SIZES: dict[str, Size] = {
    "small": Size("small", mem_mb=256, nano_cpus=500_000_000, storage_gb=1),
    "medium": Size("medium", mem_mb=1024, nano_cpus=1_000_000_000, storage_gb=5),
    "large": Size("large", mem_mb=4096, nano_cpus=2_000_000_000, storage_gb=20),
}


def size_for(name: str) -> Size:
    try:
        return SIZES[name]
    except KeyError:
        raise BadRequest(f"unknown size '{name}'; choose one of {sorted(SIZES)}")


class RuntimeStatus(str, Enum):
    ABSENT = "absent"       # nothing exists in the data plane
    RUNNING = "running"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class InstanceSpec:
    instance_id: str
    image: str
    size: Size
    host_port: int
    bind_host: str
    superuser: str
    password: str
    team_id: str
    database: str = "app"
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Provisioned:
    container_id: str


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@runtime_checkable
class Provisioner(Protocol):
    """Five lifecycle verbs (ADR-002) plus the exec/introspection the reconciler
    and backup jobs need. Every method must be idempotent — handlers may retry."""

    def create(self, spec: InstanceSpec) -> Provisioned: ...
    def destroy(self, instance_id: str) -> None: ...
    def stop(self, instance_id: str) -> None: ...
    def start(self, instance_id: str) -> None: ...
    def resize(self, instance_id: str, size: Size) -> None: ...
    def patch(self, spec: InstanceSpec) -> Provisioned: ...
    def status(self, instance_id: str) -> RuntimeStatus: ...
    def exec(self, instance_id: str, argv: list[str], *, user: str | None = None) -> ExecResult: ...
    def list_managed(self) -> list[str]: ...
