"""Append-only audit trail (BRD BR-5). One tiny helper so every mutating path
records who did what, to which resource, when — without repeating boilerplate.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    session: Session,
    *,
    action: str,
    target: str,
    actor_id: str | None = None,
    team_id: str | None = None,
    detail: dict | None = None,
) -> None:
    session.add(
        AuditLog(actor_id=actor_id, team_id=team_id, action=action, target=target, detail=detail)
    )
