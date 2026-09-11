"""ChemCompute 安全与凭证管理模块

核心设计原则：
1. 单次有效、过期机制的邀请码 (SHA-256 散列存储，明文仅在创建时展示一次)
2. 节点独立随机令牌 (服务端仅存储哈希，明文由节点本地配置持有)
3. 运行时管理员密钥 (杜绝硬编码长效密钥，启动时从环境/命令行/运行时密钥文件加载或自动生成)
4. 时间恒定比对 (防时序侧信道攻击)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_invite_code(prefix: str = "cc-inv-") -> str:
    """生成具有高熵的单次使用邀请码"""
    token = secrets.token_urlsafe(24)
    return f"{prefix}{token}"


def generate_node_token(prefix: str = "cc-node-") -> str:
    """生成节点专属认证令牌"""
    token = secrets.token_urlsafe(32)
    return f"{prefix}{token}"


def generate_admin_secret(prefix: str = "cc-adm-") -> str:
    """生成随机的管理员运行时密钥"""
    token = secrets.token_urlsafe(32)
    return f"{prefix}{token}"


def hash_secret(secret: str) -> str:
    """计算密钥的 SHA-256 十六进制散列值用于安全持久化"""
    if not secret:
        raise ValueError("Secret cannot be empty")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret: str, stored_hash: str) -> bool:
    """安全校验密钥（使用 hmac.compare_digest 防止时序攻击）"""
    if not secret or not stored_hash:
        return False
    computed = hash_secret(secret)
    return hmac.compare_digest(computed, stored_hash)


def constant_time_compare(val1: str, val2: str) -> bool:
    """字符串恒定时序比对"""
    if not isinstance(val1, str) or not isinstance(val2, str) or not val1 or not val2:
        return False
    return hmac.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))


def secure_path(path: Path | str, is_dir: bool = False) -> bool:
    """
    配置目录或敏感凭证文件的访问控制列表 (ACL)。
    - Windows: 使用 icacls 禁用继承并仅向当前用户、SYSTEM 及 Administrators 授权
    - Linux / macOS: 使用 chmod 设置 0700 (目录) 或 0600 (文件)
    """
    p = Path(path).resolve()
    if not p.exists():
        return False

    if os.name == "nt":
        try:
            import subprocess
            username = os.environ.get("USERNAME")
            if not username:
                return False
            # 管理员 (S-1-5-32-544), SYSTEM (S-1-5-18), 当前用户
            perm = "(OI)(CI)F" if is_dir else "F"
            cmd = [
                "icacls.exe",
                str(p),
                "/inheritance:r",
                "/grant:r",
                f"*S-1-5-32-544:{perm}",
                f"*S-1-5-18:{perm}",
                f"{username}:{perm}",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug("Windows 设置 ACL 异常 (非致命): %s", e)
            return False
    else:
        try:
            mode = 0o700 if is_dir else 0o600
            os.chmod(p, mode)
            return True
        except Exception as e:
            logger.debug("POSIX 设置文件权限异常: %s", e)
            return False


def ensure_secure_directory(dir_path: Path | str) -> Path:
    """创建受 ACL 保护的运行时/配置目录"""
    p = Path(dir_path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    secure_path(p, is_dir=True)
    return p


def load_or_create_runtime_admin_secret(
    explicit_secret: str | None = None,
    secret_file_path: Path | str | None = None,
) -> tuple[str, bool]:
    """
    加载或创建控制端运行时管理员密钥。
    优先级：
    1. 显式指定的 secret (CLI 或配置)
    2. 环境变量 CHEMCOMPUTE_ADMIN_SECRET
    3. 运行时密钥文件 (如存在则读取)
    4. 自动生成全新安全密钥并持久化到受保护的文件中

    返回: (admin_secret, is_newly_generated)
    """
    # 1. 显式指定
    if explicit_secret and explicit_secret.strip():
        return explicit_secret.strip(), False

    # 2. 环境变量
    env_secret = os.environ.get("CHEMCOMPUTE_ADMIN_SECRET")
    if env_secret and env_secret.strip():
        return env_secret.strip(), False

    # 确定密钥文件存储路径
    if secret_file_path:
        target_path = Path(secret_file_path)
    else:
        # 默认放在工作目录或用户目录的运行时数据文件夹
        runtime_dir = Path("data")
        ensure_secure_directory(runtime_dir)
        target_path = runtime_dir / "chemcompute-admin.secret"

    # 3. 如果文件存在且非空，直接读取
    if target_path.exists():
        try:
            content = target_path.read_text(encoding="utf-8").strip()
            if content:
                secure_path(target_path, is_dir=False)
                return content, False
        except Exception as e:
            logger.warning("无法读取现存运行时管理员密钥文件 %s: %s", target_path, e)

    # 4. 生成新密钥并安全写入
    new_secret = generate_admin_secret()
    try:
        ensure_secure_directory(target_path.parent)
        target_path.write_text(new_secret, encoding="utf-8")
        secure_path(target_path, is_dir=False)
        logger.info("已生成全新的运行时管理员密钥并保存至 %s", target_path)
    except Exception as e:
        logger.error("无法将管理员密钥持久化至文件 %s: %s", target_path, e)

    return new_secret, True
