"""Small helpers for running SQL inside a managed instance.

These are Postgres-and-exec specific by nature, but they go through the generic
``Provisioner.exec`` seam so the abstraction stays intact (a k8s driver exec works
identically). Executed over the container's local socket as the ``postgres`` OS
user, which maps to the superuser via the official image's local-trust pg_hba —
so no password is needed here.
"""

from __future__ import annotations

import time

from app.provisioner import ExecResult, Provisioner

DATABASE = "app"

# Credential rotation and DB-user management (the only callers of `psql`) run
# synchronously in the request thread, unlike every other instance mutation,
# which goes through the job queue and gets retries with backoff for free
# (DR-3) — they have to, since the response carries a one-time secret that
# doesn't exist until the SQL has actually run. That leaves them with zero
# retry against a transient failure (e.g. the container mid-restart) unless we
# add one here. A short bounded retry closes that gap without turning these
# into async operations, which would break the one-time-reveal contract.
_RETRY_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1


def _exec_sql(provisioner: Provisioner, instance_id: str, superuser: str, sql: str) -> ExecResult:
    return provisioner.exec(
        instance_id,
        ["psql", "-v", "ON_ERROR_STOP=1", "-U", superuser, "-d", DATABASE, "-c", sql],
        user="postgres",
    )


def psql(provisioner: Provisioner, instance_id: str, superuser: str, sql: str) -> ExecResult:
    result = _exec_sql(provisioner, instance_id, superuser, sql)
    for _ in range(_RETRY_ATTEMPTS - 1):
        if result.ok:
            break
        time.sleep(_RETRY_DELAY_SECONDS)
        result = _exec_sql(provisioner, instance_id, superuser, sql)
    return result


def pg_isready(provisioner: Provisioner, instance_id: str, superuser: str) -> bool:
    return provisioner.exec(
        instance_id, ["pg_isready", "-U", superuser, "-d", DATABASE], user="postgres"
    ).ok
