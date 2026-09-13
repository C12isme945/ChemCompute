"""ChemCompute 节点注册、心跳与资产清单管理路由"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from chemcompute import __version__
from chemcompute.common.models import (
    JobSpec,
    NodeHeartbeatRequest,
    NodeHeartbeatResponse,
    NodeItem,
    NodeRegisterRequest,
    NodeRegisterResponse,
)
from chemcompute.common.security import generate_node_token, hash_secret
from chemcompute.controller.db import Database, utc_now_iso
from chemcompute.controller.routes.auth import get_db, verify_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


def get_client_ip(request: Request) -> str:
    """提取客户端实际访问 IP"""
    return request.client.host if request.client else "127.0.0.1"


async def verify_node_auth(
    node_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    db: Database = Depends(get_db),
) -> bool:
    """验证节点专属认证令牌 (Bearer Token 散列比对)"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="节点认证失败：缺失 Bearer 令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not db.verify_node_token(node_id, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="节点认证失败：令牌无效或节点未注册",
        )
    return True


@router.post("/register", response_model=NodeRegisterResponse)
async def register_node(
    payload: NodeRegisterRequest,
    request: Request,
    db: Database = Depends(get_db),
) -> NodeRegisterResponse:
    """
    计算节点首次注册：
    1. 验证并原子消耗单次使用邀请码
    2. 生成节点专用随机令牌并计算其散列
    3. 服务端持久化令牌散列，仅在本次响应明文返回令牌一次
    """
    node_id = f"node-{uuid.uuid4().hex[:12]}"
    client_ip = get_client_ip(request)

    # 1. 验证并消耗邀请码
    is_valid = db.verify_and_consume_invite(payload.invite_code.strip(), node_id)
    if not is_valid:
        db.add_audit_log(
            "unknown_node",
            "REGISTER_REJECTED",
            f"邀请码无效、过期或已被使用，来源IP: {client_ip}",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="注册失败：邀请码无效、已过期或已被消耗",
        )

    # 2. 生成节点密钥并哈希
    cleartext_token = generate_node_token()
    token_hash = hash_secret(cleartext_token)

    # 3. 登记节点资产信息
    node_name = payload.node_name.strip() or payload.hostname or node_id
    db.register_node(
        node_id=node_id,
        name=node_name,
        token_hash=token_hash,
        ip_address=client_ip,
        hostname=payload.hostname,
        os_str=payload.os,
        python_version=payload.software.python_version,
        hardware_data=payload.hardware.model_dump(),
        software_data=payload.software.model_dump(),
    )

    db.add_audit_log(
        f"node:{node_id}",
        "REGISTER_SUCCESS",
        f"节点 {node_name} ({payload.hostname}) 成功注册，IP: {client_ip}",
    )

    heartbeat_interval = getattr(request.app.state, "heartbeat_interval_seconds", 15)

    return NodeRegisterResponse(
        node_id=node_id,
        node_token=cleartext_token,
        controller_version=__version__,
        heartbeat_interval_seconds=heartbeat_interval,
    )


@router.post("/{node_id}/heartbeat", response_model=NodeHeartbeatResponse)
async def node_heartbeat(
    node_id: str,
    payload: NodeHeartbeatRequest,
    request: Request,
    _: bool = Depends(verify_node_auth),
    db: Database = Depends(get_db),
) -> NodeHeartbeatResponse:
    """
    节点心跳上报：
    1. 经过鉴权后更新节点最新状态、硬件度量 (CPU/RAM/GPU) 及软件清单
    2. 检索并下发已排队的 GROMACS 作业任务规范
    """
    if payload.node_id != node_id:
        raise HTTPException(status_code=400, detail="Node ID mismatch")
    client_ip = get_client_ip(request)
    db.update_node_heartbeat(
        node_id=node_id,
        status=payload.status,
        ip_address=client_ip,
        hardware_data=payload.hardware.model_dump(),
        software_data=payload.software.model_dump(),
    )

    # 查询待分发作业
    pending_jobs_data = db.get_pending_jobs_for_node(node_id) if payload.status == "online" and not (payload.hardware.contribution or {}).get("paused") else []
    assigned_jobs: list[JobSpec] = []
    for j in pending_jobs_data:
        db.mark_job_running(j["job_id"], node_id)
        assigned_jobs.append(
            JobSpec(
                job_id=j["job_id"],
                subcommand=j["subcommand"],
                arguments=j.get("arguments", []),
                timeout_seconds=j.get("timeout_seconds", 300),
                description=j.get("description"),
            )
        )

    return NodeHeartbeatResponse(
        status="ok",
        server_time=utc_now_iso(),
        assigned_jobs=assigned_jobs,
    )


@router.get("", response_model=list[NodeItem])
async def list_nodes(
    request: Request,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> list[NodeItem]:
    """列出所有受纳计算节点及实时资产健康度"""
    offline_threshold = getattr(request.app.state, "node_offline_threshold_seconds", 45)
    nodes_raw = db.list_nodes(offline_threshold_seconds=offline_threshold)
    return [NodeItem(**n) for n in nodes_raw]


@router.get("/{node_id}", response_model=NodeItem)
async def get_node_detail(
    node_id: str,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> NodeItem:
    """获取单个节点的完整硬件与软件详情"""
    node_raw = db.get_node(node_id)
    if not node_raw:
        raise HTTPException(status_code=404, detail="节点不存在")
    return NodeItem(**node_raw)


@router.delete("/{node_id}")
async def delete_node(
    node_id: str,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> dict[str, str]:
    """注销/删除计算节点"""
    success = db.delete_node(node_id)
    if not success:
        raise HTTPException(status_code=404, detail="节点不存在")
    db.add_audit_log("admin", "DELETE_NODE", f"删除节点 {node_id}")
    return {"status": "ok", "message": f"节点 {node_id} 已注销"}
