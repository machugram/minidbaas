"""Credential rotation endpoint (ADR-005). Returns the new password once."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_principal, db_session, provisioner
from app.models import Principal
from app.provisioner import Provisioner
from app.schemas import CredentialRotatedSecret
from app.services import credentials as svc

router = APIRouter(prefix="/instances/{instance_id}/credentials", tags=["credentials"])


@router.post("/rotate", response_model=CredentialRotatedSecret)
def rotate_credential(
    instance_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(db_session),
    prov: Provisioner = Depends(provisioner),
) -> CredentialRotatedSecret:
    username, password, rotated_at = svc.rotate_credential(session, prov, principal, instance_id)
    session.commit()
    return CredentialRotatedSecret(username=username, password=password, rotated_at=rotated_at)
