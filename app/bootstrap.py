"""Identity bootstrap. Creating a principal is the one operation that can't be
done through the authenticated API (chicken-and-egg), so it lives here and is
reused by the CLI ``mdbaas init`` and the test fixtures.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Principal, Role, Team, TeamMembership
from app.security import generate_api_key, hash_api_key


def create_principal(session: Session, name: str, *, is_platform_admin: bool = False) -> tuple[Principal, str]:
    """Returns (principal, api_key). The key is shown once and only the hash is stored."""
    principal_id = uuid.uuid4().hex
    api_key = generate_api_key(principal_id)
    principal = Principal(
        id=principal_id, name=name, api_key_hash=hash_api_key(api_key), is_platform_admin=is_platform_admin
    )
    session.add(principal)
    session.flush()
    return principal, api_key


def add_member(session: Session, team: Team, principal: Principal, role: Role) -> None:
    session.add(TeamMembership(team_id=team.id, principal_id=principal.id, role=role.value))


def ensure_seed(session: Session) -> str | None:
    """Idempotently create a default team + admin if the system is empty. Returns the
    admin API key on first run, else None. Handy for local demos."""
    if session.query(Principal).first() is not None:
        return None
    team = Team(name="default")
    session.add(team)
    session.flush()
    admin, api_key = create_principal(session, "admin", is_platform_admin=True)
    add_member(session, team, admin, Role.OWNER)
    return api_key
