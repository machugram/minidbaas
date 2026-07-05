"""Small helpers for running SQL inside a managed instance.

These are Postgres-and-exec specific by nature, but they go through the generic
``Provisioner.exec`` seam so the abstraction stays intact (a k8s driver exec works
identically). Executed over the container's local socket as the ``postgres`` OS
user, which maps to the superuser via the official image's local-trust pg_hba —
so no password is needed here.
"""

from __future__ import annotations

from app.provisioner import ExecResult, Provisioner

DATABASE = "app"


def psql(provisioner: Provisioner, instance_id: str, superuser: str, sql: str) -> ExecResult:
    return provisioner.exec(
        instance_id,
        ["psql", "-v", "ON_ERROR_STOP=1", "-U", superuser, "-d", DATABASE, "-c", sql],
        user="postgres",
    )


def pg_isready(provisioner: Provisioner, instance_id: str, superuser: str) -> bool:
    return provisioner.exec(
        instance_id, ["pg_isready", "-U", superuser, "-d", DATABASE], user="postgres"
    ).ok
