"""ChemCompute FastAPI 控制端主应用"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chemcompute import __version__
from chemcompute.common.config import ControllerConfig
from chemcompute.common.security import load_or_create_runtime_admin_secret
from chemcompute.controller.db import Database
from chemcompute.controller.routes.auth import router as auth_router
from chemcompute.controller.routes.jobs import router as jobs_router
from chemcompute.controller.routes.nodes import router as nodes_router
from chemcompute.controller.routes.web import router as web_router

logger = logging.getLogger("chemcompute.controller")


def create_app(config: ControllerConfig | None = None) -> FastAPI:
    """构建并配置 FastAPI 控制端实例"""
    if config is None:
        config = ControllerConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 1. 初始化持久化 SQLite
        app.state.db = Database(config.db_path)
        app.state.node_offline_threshold_seconds = config.node_offline_threshold_seconds
        app.state.heartbeat_interval_seconds = config.heartbeat_interval_seconds

        # 2. 安全装载或生成运行时管理员密钥
        admin_secret, is_new = load_or_create_runtime_admin_secret(
            explicit_secret=config.admin_secret,
            secret_file_path=config.secret_file,
        )
        app.state.admin_secret = admin_secret

        banner = [
            "=" * 60,
            f" ChemCompute Controller v{__version__} 启动成功",
            f" 监听地址: http://{config.host}:{config.port}",
            f" SQLite 数据库: {config.db_path}",
        ]
        if is_new:
            banner.extend([
                " [安全提示] 已为您生成全新运行时管理员密钥:",
                f" 密钥已保存至本地: {config.secret_file}",
            ])
        else:
            banner.append(" [安全提示] 管理员密钥已自配置/环境变量或已有密钥文件安全加载")
        banner.append("=" * 60)
        logger.info("\n".join(banner))

        yield

        logger.info("ChemCompute 控制端正在关闭...")

    app = FastAPI(
        title="ChemCompute 控制中心",
        description="现代化化学计算与GROMACS分布式节点调度平台",
        version=__version__,
        lifespan=lifespan,
    )

    # 挂载静态资源
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 注册路由模块
    app.include_router(web_router)
    app.include_router(auth_router)
    app.include_router(nodes_router)
    app.include_router(jobs_router)

    return app
