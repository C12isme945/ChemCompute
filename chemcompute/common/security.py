"""
Security, token generation, and invite code utilities for ChemCompute.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import string
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_invite_code(prefix: str = "CC") -> str:
    """Generate a clean 6-character uppercase alphanumeric invite code like CC-7F4A9K."""
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"{prefix}-{suffix}"


def generate_token(length: int = 32) -> str:
    """Generate a high-entropy secret token for nodes."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """SHA-256 hash a token for safe persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_invite_code_format(code: str) -> bool:
    """Validate if code matches standard format CC-XXXXXX."""
    parts = code.strip().upper().split("-")
    if len(parts) != 2:
        return False
    if parts[0] != "CC" or len(parts[1]) != 6:
        return False
    return True


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
    """
    if explicit_secret and explicit_secret.strip():
        return explicit_secret.strip(), False

    env_secret = os.environ.get("CHEMCOMPUTE_ADMIN_SECRET")
    if env_secret and env_secret.strip():
        return env_secret.strip(), False

    if secret_file_path:
        target_path = Path(secret_file_path)
    else:
        runtime_dir = Path("data")
        ensure_secure_directory(runtime_dir)
        target_path = runtime_dir / "chemcompute-admin.secret"

    if target_path.exists():
        try:
            content = target_path.read_text(encoding="utf-8").strip()
            if content:
                secure_path(target_path, is_dir=False)
                return content, False
        except Exception as e:
            logger.warning("无法读取现存运行时管理员密钥文件 %s: %s", target_path, e)

    new_secret = generate_admin_secret()
    try:
        ensure_secure_directory(target_path.parent)
        target_path.write_text(new_secret, encoding="utf-8")
        secure_path(target_path, is_dir=False)
        logger.info("已生成全新的运行时管理员密钥并保存至 %s", target_path)
    except Exception as e:
        logger.error("无法将管理员密钥持久化至文件 %s: %s", target_path, e)

    return new_secret, True
