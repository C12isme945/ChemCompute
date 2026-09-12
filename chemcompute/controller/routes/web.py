"""ChemCompute 中文 Web 控制台路由"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from chemcompute import __version__
from chemcompute.common.models import AuditLogItem
from chemcompute.controller.db import Database
from chemcompute.controller.routes.auth import get_db, verify_admin

router = APIRouter(tags=["web"])

template_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(template_dir), autoescape=True)


@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request) -> HTMLResponse:
    """ChemCompute Web 管理控制台主页"""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "version": __version__,
        },
    )


@router.get("/api/v1/overview")
async def get_overview_stats(
    request: Request,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """获取系统仪表盘概览统计指标"""
    offline_threshold = getattr(request.app.state, "node_offline_threshold_seconds", 45)
    nodes = db.list_nodes(offline_threshold_seconds=offline_threshold)
    jobs = db.list_jobs(limit=100)

    total_nodes = len(nodes)
    online_nodes = sum(1 for n in nodes if n["status"] == "online")
    busy_nodes = sum(1 for n in nodes if n["status"] == "busy")

    total_gpus = 0
    total_cpu_cores = 0
    total_ram_gb = 0.0

    for n in nodes:
        hw = n.get("hardware_info", {})
        total_cpu_cores += hw.get("cpu_count_logical", 0)
        total_ram_gb += hw.get("ram_total_mb", 0) / 1024.0
        gpus = hw.get("gpus", [])
        total_gpus += len(gpus)

    running_jobs = sum(1 for j in jobs if j["status"] == "running")
    pending_jobs = sum(1 for j in jobs if j["status"] == "pending")

    return {
        "total_nodes": total_nodes,
        "online_nodes": online_nodes,
        "busy_nodes": busy_nodes,
        "total_gpus": total_gpus,
        "total_cpu_cores": total_cpu_cores,
        "total_ram_gb": round(total_ram_gb, 1),
        "running_jobs": running_jobs,
        "pending_jobs": pending_jobs,
        "total_jobs": len(jobs),
    }


@router.get("/api/v1/audit-logs", response_model=list[AuditLogItem])
async def list_audit_logs(
    limit: int = 100,
    _: bool = Depends(verify_admin),
    db: Database = Depends(get_db),
) -> list[AuditLogItem]:
    """获取平台安全审计日志"""
    logs = db.list_audit_logs(limit=limit)
    return [AuditLogItem(**entry) for entry in logs]
