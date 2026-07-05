"""Shared FastAPI dependencies: DB session, authenticated principal, provisioner."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import Unauthorized
from app.models import Principal
from app.provisioner import Provisioner
from app.runtime import get_provisioner
from app.security import parse_principal_id, verify_api_key


def db_session() -> Session:
    yield from get_session()


def current_principal(
    authorization: str | None = Header(default=None),
    session: Session = Depends(db_session),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    api_key = authorization.split(" ", 1)[1].strip()
    principal_id = parse_principal_id(api_key)
    if not principal_id:
        raise Unauthorized("malformed api key")
    principal = session.get(Principal, principal_id)
    # verify_api_key is constant-time; a missing principal still fails as unauthorized.
    if principal is None or not verify_api_key(api_key, principal.api_key_hash):
        raise Unauthorized("invalid api key")
    return principal


def provisioner() -> Provisioner:
    return get_provisioner()
