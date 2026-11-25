"""Human-only approval endpoints. Access requires the owner's bearer token."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.middleware import get_current_user
from . import ledger

router = APIRouter(prefix="/api/agent-security", tags=["Agent Security"])


class Approval(BaseModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("/approvals/{approval_id}")
def preview(approval_id: str, user=Depends(get_current_user)):
    try:
        return ledger.view(user.id, approval_id)
    except LookupError:
        raise HTTPException(404, "approval unavailable") from None


@router.post("/approvals/{approval_id}/approve")
def confirm(approval_id: str, body: Approval, user=Depends(get_current_user)):
    if not ledger.admit(user.id, "approve", 30):
        raise HTTPException(429, "approval limit exceeded")
    try:
        ledger.approve(user.id, approval_id, body.digest)
    except LookupError:
        raise HTTPException(404, "approval unavailable") from None
    return {"approved": True}


@router.get("/approvals")
def pending(user=Depends(get_current_user)):
    return {"ids": ledger.pending(user.id)}
