"""In-memory ``Provisioner`` so the whole suite runs without Docker. It honours the
same contract as ``DockerProvisioner`` (ADR-002) — which is exactly why the
production code can be tested against it."""

from __future__ import annotations

from app.provisioner import ExecResult, InstanceSpec, Provisioned, RuntimeStatus, Size


class FakeProvisioner:
    def __init__(self) -> None:
        self.instances: dict[str, dict] = {}

    def create(self, spec: InstanceSpec) -> Provisioned:
        self.instances[spec.instance_id] = {"running": True, "image": spec.image}
        return Provisioned(container_id=f"fake-{spec.instance_id}")

    def destroy(self, instance_id: str) -> None:
        self.instances.pop(instance_id, None)

    def stop(self, instance_id: str) -> None:
        if instance_id in self.instances:
            self.instances[instance_id]["running"] = False

    def start(self, instance_id: str) -> None:
        if instance_id in self.instances:
            self.instances[instance_id]["running"] = True

    def resize(self, instance_id: str, size: Size) -> None:
        pass

    def patch(self, spec: InstanceSpec) -> Provisioned:
        self.instances[spec.instance_id] = {"running": True, "image": spec.image}
        return Provisioned(container_id=f"fake-{spec.instance_id}")

    def status(self, instance_id: str) -> RuntimeStatus:
        rec = self.instances.get(instance_id)
        if rec is None:
            return RuntimeStatus.ABSENT
        return RuntimeStatus.RUNNING if rec["running"] else RuntimeStatus.STOPPED

    def exec(self, instance_id: str, argv: list[str], *, user: str | None = None) -> ExecResult:
        if instance_id not in self.instances:
            return ExecResult(127, "container not found")
        # Emulate the `stat -c%s` size probe used by backups.
        if argv[:2] == ["sh", "-c"] and "stat -c%s" in argv[2]:
            return ExecResult(0, "1024")
        return ExecResult(0, "")  # pg_isready, psql, pg_dump, etc. all succeed

    def list_managed(self) -> list[str]:
        return list(self.instances.keys())
