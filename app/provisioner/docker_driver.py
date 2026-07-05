"""``DockerProvisioner`` — the only module that imports the Docker SDK (ADR-002).

Resource naming is derived purely from ``instance_id`` so the driver is
stateless: given an id it can always find (or recreate) the container and volume.
Every managed object carries ``mdbaas.*`` labels, which are the ground truth the
reconciler uses to discover reality independent of the metadata DB (ARCHITECTURE 2.2).
"""

from __future__ import annotations

import docker
from docker.errors import APIError, NotFound

from app.config import get_settings
from app.errors import ProvisioningError
from app.provisioner.base import ExecResult, InstanceSpec, Provisioned, RuntimeStatus, Size

_PGDATA = "/var/lib/postgresql/data"
_BACKUPS_MOUNT = "/backups"

# Postgres' entrypoint needs a handful of capabilities (chown the data dir, drop
# privileges to the postgres user). We drop everything else and forbid privilege
# escalation — a small, honest hardening step (design-review 1.2).
_CAP_ADD = ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER"]
_SECURITY_OPT = ["no-new-privileges:true"]


class DockerProvisioner:
    def __init__(self, client: docker.DockerClient | None = None):
        settings = get_settings()
        self._backups_volume = settings.backups_volume
        if client is not None:
            self._client = client
        elif settings.docker_base_url:
            self._client = docker.DockerClient(base_url=settings.docker_base_url)
        else:
            self._client = docker.from_env()

    # -- naming ------------------------------------------------------------- #
    @staticmethod
    def _container_name(instance_id: str) -> str:
        return f"mdbaas-pg-{instance_id}"

    @staticmethod
    def _volume_name(instance_id: str) -> str:
        return f"mdbaas-vol-{instance_id}"

    @staticmethod
    def _labels(spec: InstanceSpec) -> dict[str, str]:
        return {
            "mdbaas.managed": "true",
            "mdbaas.instance_id": spec.instance_id,
            "mdbaas.team": spec.team_id,
        }

    def _get_container(self, instance_id: str):
        try:
            return self._client.containers.get(self._container_name(instance_id))
        except NotFound:
            return None

    # -- lifecycle ---------------------------------------------------------- #
    def create(self, spec: InstanceSpec) -> Provisioned:
        # Idempotent: if a container already exists for this id, converge to it
        # rather than erroring — the job that created it may be retrying (DR-3).
        existing = self._get_container(spec.instance_id)
        if existing is not None:
            if existing.status != "running":
                existing.start()
            return Provisioned(container_id=existing.id)

        self._ensure_volume(self._volume_name(spec.instance_id), self._labels(spec))
        self._ensure_volume(self._backups_volume, {"mdbaas.managed": "true"})
        try:
            container = self._client.containers.run(
                image=spec.image,
                name=self._container_name(spec.instance_id),
                detach=True,
                environment={
                    "POSTGRES_USER": spec.superuser,
                    "POSTGRES_PASSWORD": spec.password,
                    "POSTGRES_DB": spec.database,
                    # keep the data dir a subfolder so the volume root stays clean
                    "PGDATA": f"{_PGDATA}/pgdata",
                },
                # ADR-007: bind to a single localhost host port.
                ports={"5432/tcp": (spec.bind_host, spec.host_port)},
                volumes={
                    self._volume_name(spec.instance_id): {"bind": _PGDATA, "mode": "rw"},
                    self._backups_volume: {"bind": _BACKUPS_MOUNT, "mode": "rw"},
                },
                mem_limit=f"{spec.size.mem_mb}m",
                nano_cpus=spec.size.nano_cpus,
                labels=self._labels(spec),
                cap_drop=["ALL"],
                cap_add=_CAP_ADD,
                security_opt=_SECURITY_OPT,
                restart_policy={"Name": "unless-stopped"},
            )
        except APIError as exc:
            raise ProvisioningError(f"docker create failed: {exc.explanation or exc}") from exc
        return Provisioned(container_id=container.id)

    def destroy(self, instance_id: str) -> None:
        # Removes container *and* volume — this is a data-destroying delete. The
        # final-backup decision is made a layer up (services/lifecycle), not here.
        container = self._get_container(instance_id)
        if container is not None:
            container.remove(force=True)
        try:
            self._client.volumes.get(self._volume_name(instance_id)).remove(force=True)
        except NotFound:
            pass

    def stop(self, instance_id: str) -> None:
        container = self._get_container(instance_id)
        if container is not None and container.status == "running":
            container.stop(timeout=30)

    def start(self, instance_id: str) -> None:
        container = self._get_container(instance_id)
        if container is not None and container.status != "running":
            container.start()

    def resize(self, instance_id: str, size: Size) -> None:
        # CPU/memory are live-updatable. Storage is intentionally not touched here:
        # the local volume driver can't grow/cap it (DR-8) — that's a soft limit.
        container = self._get_container(instance_id)
        if container is None:
            return
        container.update(mem_limit=f"{size.mem_mb}m", nano_cpus=size.nano_cpus)

    def patch(self, spec: InstanceSpec) -> Provisioned:
        # Image swap on the same volume (ADR-004): drop the old container, keep the
        # data volume, recreate on the new image. create() reuses the volume.
        container = self._get_container(spec.instance_id)
        if container is not None:
            container.remove(force=True)
        return self.create(spec)

    # -- introspection ------------------------------------------------------ #
    def status(self, instance_id: str) -> RuntimeStatus:
        container = self._get_container(instance_id)
        if container is None:
            return RuntimeStatus.ABSENT
        if container.status == "running":
            # "running" only means the container is up; readiness is confirmed by
            # the provision handler via pg_isready.
            return RuntimeStatus.RUNNING
        return RuntimeStatus.STOPPED

    def exec(self, instance_id: str, argv: list[str], *, user: str | None = None) -> ExecResult:
        container = self._get_container(instance_id)
        if container is None:
            return ExecResult(exit_code=127, output="container not found")
        exit_code, output = container.exec_run(cmd=argv, user=user or "postgres")
        return ExecResult(exit_code=exit_code, output=output.decode(errors="replace"))

    def list_managed(self) -> list[str]:
        containers = self._client.containers.list(
            all=True, filters={"label": "mdbaas.managed=true"}
        )
        return [c.labels["mdbaas.instance_id"] for c in containers if "mdbaas.instance_id" in c.labels]

    # -- helpers ------------------------------------------------------------ #
    def _ensure_volume(self, name: str, labels: dict[str, str]) -> None:
        try:
            self._client.volumes.get(name)
        except NotFound:
            self._client.volumes.create(name=name, labels=labels)
