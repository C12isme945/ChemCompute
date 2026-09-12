"""ChemCompute 配置管理模块 (YAML / 环境变量解析)"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class ControllerConfig(BaseModel):
    """控制端运行配置 (默认仅绑定本地环回 127.0.0.1，防未授权暴露)"""
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = "data/chemcompute.db"
    admin_secret: str | None = None
    secret_file: str = "data/chemcompute-admin.secret"
    node_offline_threshold_seconds: int = 45
    heartbeat_interval_seconds: int = 15


class NodeConfig(BaseModel):
    """计算节点运行配置"""
    controller_url: str = "http://127.0.0.1:8000"
    node_name: str = ""
    node_id: str | None = None
    node_token: str | None = None
    invite_code: str | None = None
    heartbeat_interval_seconds: int = 15
    gromacs_custom_path: str | None = None
    workspace_dir: str = "data/workspace"
    max_job_timeout_seconds: int = 3600


def load_yaml_config(path: str | Path, model_cls: type[BaseModel]) -> BaseModel:
    """从 YAML 文件读取配置，如文件不存在则返回默认配置对象"""
    p = Path(path)
    if not p.exists():
        return model_cls()

    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return model_cls(**data)
    except Exception as e:
        raise RuntimeError(f"解析配置文件 {p} 失败: {e}") from e


def save_yaml_config(config: BaseModel, path: str | Path) -> None:
    """将配置对象安全写入 YAML 文件并施加访问控制"""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(config.model_dump(), f, allow_unicode=True, sort_keys=False)
