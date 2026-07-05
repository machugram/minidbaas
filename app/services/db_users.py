"""Managed database roles *inside* an instance (ARCHITECTURE 2.5). Usernames are
validated as SQL identifiers at the schema layer, and passwords come from a
quote-free alphabet, so the composed DDL is safe to inline.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.access import require_role
from app.errors import Conflict, NotFound, ProvisioningError
from app.models import DbUser, Principal, Role
from app.provisioner import Provisioner
from app.schemas import DbUserCreate
from app.security import generate_password
from app.services import pgops
from app.services.instances import get_instance

_DB = pgops.DATABASE


def _grant_ddl(username: str, password: str, privileges: str) -> str:
    stmts = [
        f"CREATE ROLE {username} LOGIN PASSWORD '{password}'",
        f"GRANT CONNECT ON DATABASE {_DB} TO {username}",
        f"GRANT USAGE ON SCHEMA public TO {username}",
    ]
    if privileges == "readonly":
        stmts += [
            f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {username}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {username}",
        ]
    else:  # readwrite
        stmts += [
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {username}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {username}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {username}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {username}",
        ]
    return "; ".join(stmts)


def create_db_user(
    session: Session, provisioner: Provisioner, principal: Principal, instance_id: str, req: DbUserCreate
) -> tuple[str, str, str]:
    """Returns (username, privileges, one_time_password)."""
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.MEMBER)
    if session.scalar(
        select(DbUser.id).where(DbUser.instance_id == instance_id, DbUser.username == req.username)
    ):
        raise Conflict(f"db user '{req.username}' already exists")

    password = generate_password()
    superuser = instance.credential.username
    result = pgops.psql(provisioner, instance.id, superuser, _grant_ddl(req.username, password, req.privileges))
    if not result.ok:
        raise ProvisioningError(f"failed to create db user: {result.output[:200]}")

    session.add(DbUser(instance_id=instance_id, username=req.username, privileges=req.privileges))
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="db_user.create", target=f"{instance_id}/{req.username}")
    return req.username, req.privileges, password


def drop_db_user(
    session: Session, provisioner: Provisioner, principal: Principal, instance_id: str, username: str
) -> None:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.ADMIN)
    row = session.scalar(
        select(DbUser).where(DbUser.instance_id == instance_id, DbUser.username == username)
    )
    if row is None:
        raise NotFound(f"db user '{username}' not found")

    superuser = instance.credential.username
    # Reassign/drop owned objects first, else DROP ROLE fails if the role owns anything.
    ddl = (
        f"REASSIGN OWNED BY {username} TO {superuser}; "
        f"DROP OWNED BY {username}; "
        f"DROP ROLE IF EXISTS {username}"
    )
    result = pgops.psql(provisioner, instance.id, superuser, ddl)
    if not result.ok:
        raise ProvisioningError(f"failed to drop db user: {result.output[:200]}")

    session.delete(row)
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="db_user.drop", target=f"{instance_id}/{username}")
