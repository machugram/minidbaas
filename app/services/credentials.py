"""Credential rotation (ADR-005). Runs synchronously: an ``ALTER ROLE`` inside the
instance, then re-encrypt the stored copy and audit. Generated passwords use an
alphabet with no quotes/backslashes, so inlining into the SQL is injection-safe.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app import audit
from app.access import require_role
from app.clock import utcnow
from app.errors import ProvisioningError
from app.models import Principal, Role
from app.provisioner import Provisioner
from app.security import encrypt_secret, generate_password
from app.services import pgops
from app.services.instances import get_instance


def rotate_credential(
    session: Session, provisioner: Provisioner, principal: Principal, instance_id: str
) -> tuple[str, str, datetime]:
    instance = get_instance(session, principal, instance_id)
    require_role(session, principal, instance.team_id, Role.ADMIN)
    cred = instance.credential
    new_password = generate_password()

    result = pgops.psql(
        provisioner, instance.id, cred.username,
        f"ALTER ROLE {cred.username} PASSWORD '{new_password}'",
    )
    if not result.ok:
        raise ProvisioningError(f"failed to rotate credential: {result.output[:200]}")

    cred.secret_encrypted = encrypt_secret(new_password)
    cred.rotated_at = utcnow()
    audit.record(session, actor_id=principal.id, team_id=instance.team_id,
                 action="credential.rotate", target=instance_id)
    return cred.username, new_password, cred.rotated_at
