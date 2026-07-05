"""Process-wide provisioner handle.

A single lazily-constructed ``DockerProvisioner`` is shared by request handlers
(synchronous ops like credential rotation) and the scheduler. Tests call
``set_provisioner`` to inject a fake, keeping the whole suite Docker-free.
"""

from __future__ import annotations

from app.provisioner import Provisioner

_provisioner: Provisioner | None = None


def get_provisioner() -> Provisioner:
    global _provisioner
    if _provisioner is None:
        from app.provisioner.docker_driver import DockerProvisioner  # deferred: no Docker import at test time

        _provisioner = DockerProvisioner()
    return _provisioner


def set_provisioner(provisioner: Provisioner | None) -> None:
    global _provisioner
    _provisioner = provisioner
