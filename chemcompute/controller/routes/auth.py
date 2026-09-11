"""ChemCompute 控制端鉴权与邀请码管理路由"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from chemcompute.common.models import InviteCreateRequest, InviteCreateResponse, InviteItem
from chemcompute.common.security import (
    constant_time_compare,
    generate_invite_code,
    hash_secret,
)
from chemcompute.controller.db import Database

router = APIRouter(prefix="/api/v1", tags=["auth"])


def get_db(request: Request) -> Database:
    """获取应用级数据库实例"""
    return request.app.state.db


def get_admin_secret(request: Request) -> str:
    """获取当前运行时管理员密钥"""
    return request.app.state.admin_secret


async def verify_admin(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> bool:
    """
    多途径安全验证管理员令牌：
    1. Authorization: Bearer <token>
    2. X-Admin-Token: <token>
    Cookie authentication intentionally disabled.
    """
    runtime_secret = get_admin_secret(request)
    provided_token = None

    if authorization and authorization.startswith("Bearer "):
        provided_token = authorization.split(" ", 1)[1].strip()
    elif x_admin_token:
        provided_token = x_admin_token.strip()

    if not provided_token or not constant_time_compare(provided_token, runtime_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权: 管理员密钥无效或缺失",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True


@router.post("/admin/login")
async def admin_login(
    payload: dict[str, str],
    request: Request,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """验证管理员口令并返回登录状态"""
    token = payload.get("token", "").strip()
    runtime_secret = get_admin_secret(request)
    if not token or not constant_time_compare(token, runtime_secret):
        db.add_audit_log("system", "ADMIN_LOGIN_FAILED", "管理员认证失败尝试")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员凭据错误",
        )
    db.add_audit_log("admin", "ADMIN_LOGIN_SUCCESS", "管理员通过 Web 或 API 登录")
    return {"status": "ok", "message": "管理员认证成功"}


@router.post("/invites", response_model=InviteCreateResponse)
async def create_invite(
    payload: InviteCreateRequest,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> InviteCreateResponse:
    """
    生成单次使用邀请码：
    - 服务端计算 SHA-256 散列并持久化
    - 明文邀请码仅在此接口响应中返回一次
    """
    raw_code = generate_invite_code()
    code_hash = hash_secret(raw_code)
    code_prefix = raw_code[:12] + "..."

    expires_dt = datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)
    expires_at_iso = expires_dt.isoformat()

    db.create_invite(
        code_hash=code_hash,
        code_prefix=code_prefix,
        expires_at=expires_at_iso,
        note=payload.note,
    )
    db.add_audit_log("admin", "CREATE_INVITE", f"创建邀请码前缀 {code_prefix}，备注: {payload.note}")

    return InviteCreateResponse(
        invite_code=raw_code,
        expires_at=expires_at_iso,
        note=payload.note,
    )


@router.get("/invites", response_model=list[InviteItem])
async def list_invites(
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> list[InviteItem]:
    """列出所有邀请码（仅展示掩码前缀）"""
    items = db.list_invites()
    return [InviteItem(**item) for item in items]


@router.delete("/invites/{invite_id}")
async def revoke_invite(
    invite_id: int,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> dict[str, str]:
    """撤销尚未使用的邀请码"""
    success = db.revoke_invite(invite_id)
    if not success:
        raise HTTPException(status_code=404, detail="邀请码不存在或已被使用，无法撤销")
    db.add_audit_log("admin", "REVOKE_INVITE", f"撤销邀请码 ID {invite_id}")
    return {"status": "ok", "message": f"邀请码 {invite_id} 已成功撤销"}
