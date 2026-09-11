"""ChemCompute GROMACS 作业任务调度与状态回传路由"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from chemcompute.common.models import (
    JobCreateRequest,
    JobItem,
    JobStatusUpdateRequest,
)
from chemcompute.controller.db import Database
from chemcompute.controller.routes.auth import get_db, verify_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

# GROMACS 允许执行的严格安全白名单子命令
ALLOWED_GROMACS_SUBCOMMANDS = {
    "version",
    "check",
    "grompp",
    "mdrun",
    "editconf",
    "solvate",
    "genion",
    "energy",
}

# 危险字符匹配正则：禁止任意管道、重定向、换行或命令链接符号、空字符
SHELL_METACHUTE_PATTERN = re.compile(r"[;&|`$<>\r\n\0]")
# 路径穿越模式防御：禁止父目录遍历
PATH_TRAVERSAL_PATTERN = re.compile(r"(\.\.[/\\]|[/\\]\.\.|^\.\.$)")


def validate_job_arguments(subcommand: str, arguments: list[str]) -> None:
    """校验作业子命令与参数格式安全性，杜绝任意系统调用注入"""
    subcmd_clean = subcommand.strip().lower()
    if subcmd_clean not in ALLOWED_GROMACS_SUBCOMMANDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法 GROMACS 子命令 '{subcommand}'。仅允许白名单指令: {sorted(ALLOWED_GROMACS_SUBCOMMANDS)}",
        )

    for arg in arguments:
        if not isinstance(arg, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="作业参数必须全部为纯文本字符串",
            )
        if SHELL_METACHUTE_PATTERN.search(arg):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"参数中检测到禁止的特殊字符或命令注入模式: '{arg}'",
            )
        if PATH_TRAVERSAL_PATTERN.search(arg):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"参数中检测到越界路径穿越风险 (..): '{arg}'",
            )


@router.post("", response_model=JobItem)
async def create_job(
    payload: JobCreateRequest,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> JobItem:
    """管理员分发 GROMACS 计算作业至目标节点"""
    validate_job_arguments(payload.subcommand, payload.arguments)

    node = db.get_node(payload.node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"目标节点 '{payload.node_id}' 不存在",
        )

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    job_record = db.create_job(
        job_id=job_id,
        node_id=payload.node_id,
        subcommand=payload.subcommand.strip().lower(),
        arguments=payload.arguments,
        timeout_seconds=payload.timeout_seconds,
        description=payload.description,
    )

    db.add_audit_log(
        "admin",
        "DISPATCH_JOB",
        f"向节点 {payload.node_id} 分发作业 {job_id}: gmx {payload.subcommand}",
    )

    return JobItem(
        job_id=job_id,
        node_id=payload.node_id,
        node_name=node.get("name"),
        subcommand=job_record["subcommand"],
        arguments=job_record["arguments"],
        status=job_record["status"],
        created_at=job_record["created_at"],
        description=job_record["description"],
    )


@router.get("", response_model=list[JobItem])
async def list_jobs(
    limit: int = 50,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> list[JobItem]:
    """获取全平台历史及进行中作业记录"""
    jobs = db.list_jobs(limit=limit)
    return [JobItem(**j) for j in jobs]


@router.get("/{job_id}", response_model=JobItem)
async def get_job_detail(
    job_id: str,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> JobItem:
    """获取单个作业执行详情与日志"""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return JobItem(**job)


@router.post("/{job_id}/status")
async def update_job_status(
    job_id: str,
    payload: JobStatusUpdateRequest,
    authorization: Annotated[str | None, Header()] = None,
    db: Database = Depends(get_db),
) -> dict[str, str]:
    """计算节点执行完毕后上报执行状态与截断日志"""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")

    node_id = job["node_id"]

    # 验证节点令牌
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="节点认证缺失",
        )
    token = authorization.split(" ", 1)[1].strip()
    if not db.verify_node_token(node_id, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无权修改该作业状态（节点凭据无效）",
        )

    # 日志大小防御截断 (最大存储 500KB)
    max_log_len = 500_000
    stdout_trimmed = (payload.stdout[:max_log_len] + "\n...[已截断]") if payload.stdout and len(payload.stdout) > max_log_len else payload.stdout
    stderr_trimmed = (payload.stderr[:max_log_len] + "\n...[已截断]") if payload.stderr and len(payload.stderr) > max_log_len else payload.stderr

    db.update_job_status(
        job_id=job_id,
        node_id=node_id,
        status=payload.status,
        exit_code=payload.exit_code,
        stdout=stdout_trimmed,
        stderr=stderr_trimmed,
        error_message=payload.error_message,
    )

    db.add_audit_log(
        f"node:{node_id}",
        "JOB_STATUS_UPDATE",
        f"作业 {job_id} 状态更新为 {payload.status} (exit_code: {payload.exit_code})",
    )

    return {"status": "ok", "job_id": job_id}
