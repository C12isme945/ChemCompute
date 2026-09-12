"""
Invite code generation and management REST endpoints.
"""

from typing import List, Dict, Any
from fastapi import APIRouter, Request
from pydantic import BaseModel

from chemcompute.common.security import generate_invite_code

router = APIRouter(prefix="/api/invite-codes", tags=["invite-codes"])


class CreateInviteCodeRequest(BaseModel):
    max_uses: int = 10
    expires_in_days: float = 7.0


class InviteCodeResponse(BaseModel):
    code: str
    max_uses: int
    expires_in_seconds: float


@router.post("", response_model=InviteCodeResponse)
def create_code(req: CreateInviteCodeRequest, request: Request):
    db = request.app.state.db
    code = generate_invite_code()
    expires_sec = req.expires_in_days * 86400
    db.create_invite_code(code=code, max_uses=req.max_uses, expires_in_seconds=expires_sec)
    return InviteCodeResponse(code=code, max_uses=req.max_uses, expires_in_seconds=expires_sec)


@router.get("", response_model=List[Dict[str, Any]])
def list_codes(request: Request):
    db = request.app.state.db
    return db.list_invite_codes()


@router.get("/active", response_model=Dict[str, Any])
def get_active_admin_code(request: Request):
    db = request.app.state.db
    code = db.get_or_create_default_admin_code()
    return {"admin_key": code, "invite_code": code}
