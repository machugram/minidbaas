"""Read-only view over the audit trail (BRD BR-5) — the primary way to answer
"who did what to this infrastructure, and when" without shelling into the
metadata DB."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session
from app.models import AuditLog, Principal
from app.schemas import AuditLogOut
from app.services import audit_log as svc

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
def list_audit(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    team_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> list[AuditLog]:
    return svc.list_entries(session, principal, team_id=team_id, limit=limit)
