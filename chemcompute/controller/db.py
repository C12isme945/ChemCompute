"""ChemCompute SQLite 持久化层

安全特性：
- 邀请码仅存储 SHA-256 散列，防数据库泄露导致未授权注册
- 节点鉴权令牌仅存储 SHA-256 散列，节点本地保管明文
- 完整的审计日志记录机制
- 数据库连接线程安全
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chemcompute.common.security import hash_secret, verify_secret

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """获取标准 UTC ISO 8601 时间戳字符串"""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """ChemCompute SQLite 数据库操作管理器"""

    def __init__(self, db_path: str | Path = "data/chemcompute.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self) -> None:
        """初始化数据表结构"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. 邀请码表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code_hash TEXT UNIQUE NOT NULL,
                    code_prefix TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    is_used INTEGER NOT NULL DEFAULT 0,
                    used_at TEXT NULL,
                    used_by_node_id TEXT NULL,
                    note TEXT NULL
                );
                """
            )

            # 2. 节点表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'online',
                    ip_address TEXT NULL,
                    hostname TEXT NOT NULL,
                    os TEXT NOT NULL,
                    python_version TEXT NOT NULL,
                    hardware_json TEXT NOT NULL,
                    software_json TEXT NOT NULL
                );
                """
            )

            # 3. 作业任务表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    subcommand TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    timeout_seconds INTEGER NOT NULL DEFAULT 300,
                    description TEXT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT NULL,
                    completed_at TEXT NULL,
                    exit_code INTEGER NULL,
                    stdout TEXT NULL,
                    stderr TEXT NULL,
                    error_message TEXT NULL,
                    FOREIGN KEY(node_id) REFERENCES nodes(node_id) ON DELETE CASCADE
                );
                """
            )

            # 4. 审计日志表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NULL
                );
                """
            )
            conn.commit()

    # ---------------- 邀请码管理 ----------------

    def create_invite(
        self,
        code_hash: str,
        code_prefix: str,
        expires_at: str,
        note: str | None = None,
    ) -> int:
        """存储散列后的邀请码"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO invites (code_hash, code_prefix, created_at, expires_at, is_used, note)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (code_hash, code_prefix, utc_now_iso(), expires_at, note),
            )
            conn.commit()
            return cursor.lastrowid

    def verify_and_consume_invite(self, cleartext_invite_code: str, node_id: str) -> bool:
        """验证邀请码有效性并将其标记为已使用 (原子操作)"""
        code_hash = hash_secret(cleartext_invite_code)
        now_iso = utc_now_iso()

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, expires_at, is_used FROM invites
                WHERE code_hash = ?
                """,
                (code_hash,),
            )
            row = cursor.fetchone()
            if not row:
                return False

            is_used = bool(row["is_used"])
            expires_at = row["expires_at"]

            # 已使用或已过期
            if is_used:
                return False

            try:
                exp_dt = datetime.fromisoformat(expires_at)
                now_dt = datetime.fromisoformat(now_iso)
                if now_dt > exp_dt:
                    return False
            except Exception:
                return False

            # 原子标记为已使用
            cursor.execute(
                """
                UPDATE invites
                SET is_used = 1, used_at = ?, used_by_node_id = ?
                WHERE id = ? AND is_used = 0
                """,
                (now_iso, node_id, row["id"]),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_invites(self) -> list[dict[str, Any]]:
        """获取邀请码列表（掩码展示）"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, code_prefix, created_at, expires_at, is_used, used_at, used_by_node_id, note
                FROM invites
                ORDER BY id DESC
                """
            )
            rows = cursor.fetchall()
            results = []
            now_dt = datetime.now(timezone.utc)
            for r in rows:
                item = dict(r)
                item["is_used"] = bool(item["is_used"])
                try:
                    exp_dt = datetime.fromisoformat(item["expires_at"])
                    item["is_expired"] = now_dt > exp_dt
                except Exception:
                    item["is_expired"] = False
                results.append(item)
            return results

    def revoke_invite(self, invite_id: int) -> bool:
        """撤销（删除）未使用的邀请码"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM invites WHERE id = ? AND is_used = 0", (invite_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ---------------- 节点管理 ----------------

    def register_node(
        self,
        node_id: str,
        name: str,
        token_hash: str,
        ip_address: str | None,
        hostname: str,
        os_str: str,
        python_version: str,
        hardware_data: dict[str, Any],
        software_data: dict[str, Any],
    ) -> None:
        """登记新节点"""
        now = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO nodes (
                    node_id, name, token_hash, registered_at, last_seen,
                    status, ip_address, hostname, os, python_version,
                    hardware_json, software_json
                )
                VALUES (?, ?, ?, ?, ?, 'online', ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    name,
                    token_hash,
                    now,
                    now,
                    ip_address,
                    hostname,
                    os_str,
                    python_version,
                    json.dumps(hardware_data, ensure_ascii=False),
                    json.dumps(software_data, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """查询特定节点信息"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["hardware_info"] = json.loads(data["hardware_json"])
            except Exception:
                data["hardware_info"] = {}
            try:
                data["software_info"] = json.loads(data["software_json"])
            except Exception:
                data["software_info"] = {}
            return data

    def verify_node_token(self, node_id: str, cleartext_token: str) -> bool:
        """恒定时序校验节点令牌散列"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token_hash FROM nodes WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return False
            return verify_secret(cleartext_token, row["token_hash"])

    def update_node_heartbeat(
        self,
        node_id: str,
        status: str,
        ip_address: str | None,
        hardware_data: dict[str, Any],
        software_data: dict[str, Any],
    ) -> bool:
        """更新节点心跳、状态及软硬件清单"""
        now = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE nodes
                SET last_seen = ?, status = ?, ip_address = COALESCE(?, ip_address),
                    hardware_json = ?, software_json = ?
                WHERE node_id = ?
                """,
                (
                    now,
                    status,
                    ip_address,
                    json.dumps(hardware_data, ensure_ascii=False),
                    json.dumps(software_data, ensure_ascii=False),
                    node_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_nodes(self, offline_threshold_seconds: int = 45) -> list[dict[str, Any]]:
        """获取所有节点，并根据最后心跳时间动态计算在线/离线状态"""
        now_dt = datetime.now(timezone.utc)
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM nodes ORDER BY last_seen DESC")
            rows = cursor.fetchall()
            nodes = []
            for r in rows:
                item = dict(r)
                try:
                    last_seen_dt = datetime.fromisoformat(item["last_seen"])
                    diff = (now_dt - last_seen_dt).total_seconds()
                    if diff > offline_threshold_seconds:
                        item["status"] = "offline"
                except Exception:
                    item["status"] = "offline"

                try:
                    item["hardware_info"] = json.loads(item["hardware_json"])
                except Exception:
                    item["hardware_info"] = {}

                try:
                    item["software_info"] = json.loads(item["software_json"])
                except Exception:
                    item["software_info"] = {}

                nodes.append(item)
            return nodes

    def delete_node(self, node_id: str) -> bool:
        """删除节点及其关联作业"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ---------------- 作业管理 ----------------

    def create_job(
        self,
        job_id: str,
        node_id: str,
        subcommand: str,
        arguments: list[str],
        timeout_seconds: int = 300,
        description: str | None = None,
    ) -> dict[str, Any]:
        """创建新计算作业"""
        now = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO jobs (
                    job_id, node_id, subcommand, arguments_json,
                    status, timeout_seconds, description, created_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    job_id,
                    node_id,
                    subcommand,
                    json.dumps(arguments, ensure_ascii=False),
                    timeout_seconds,
                    description,
                    now,
                ),
            )
            conn.commit()
            return {
                "job_id": job_id,
                "node_id": node_id,
                "subcommand": subcommand,
                "arguments": arguments,
                "status": "pending",
                "timeout_seconds": timeout_seconds,
                "description": description,
                "created_at": now,
            }

    def get_pending_jobs_for_node(self, node_id: str) -> list[dict[str, Any]]:
        """获取指定节点待分发的作业"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT job_id, subcommand, arguments_json, timeout_seconds, description
                FROM jobs
                WHERE node_id = ? AND status = 'pending'
                ORDER BY created_at ASC
                """,
                (node_id,),
            )
            rows = cursor.fetchall()
            jobs = []
            for r in rows:
                item = dict(r)
                try:
                    item["arguments"] = json.loads(item["arguments_json"])
                except Exception:
                    item["arguments"] = []
                jobs.append(item)
            return jobs

    def mark_job_running(self, job_id: str, node_id: str) -> bool:
        """将作业标记为运行中"""
        now = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?
                WHERE job_id = ? AND node_id = ? AND status = 'pending'
                """,
                (now, job_id, node_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_job_status(
        self,
        job_id: str,
        node_id: str,
        status: str,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """更新作业完成或失败状态"""
        now = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, exit_code = ?,
                    stdout = ?, stderr = ?, error_message = ?
                WHERE job_id = ? AND node_id = ?
                """,
                (status, now, exit_code, stdout, stderr, error_message, job_id, node_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """获取单个作业详情"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT j.*, n.name as node_name
                FROM jobs j
                LEFT JOIN nodes n ON j.node_id = n.node_id
                WHERE j.job_id = ?
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["arguments"] = json.loads(data["arguments_json"])
            except Exception:
                data["arguments"] = []
            return data

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取作业列表"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT j.*, n.name as node_name
                FROM jobs j
                LEFT JOIN nodes n ON j.node_id = n.node_id
                ORDER BY j.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            jobs = []
            for r in rows:
                data = dict(r)
                try:
                    data["arguments"] = json.loads(data["arguments_json"])
                except Exception:
                    data["arguments"] = []
                jobs.append(data)
            return jobs

    # ---------------- 审计日志 ----------------

    def add_audit_log(self, actor: str, action: str, details: str | None = None) -> None:
        """记录安全审计日志"""
        now = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_logs (timestamp, actor, action, details)
                VALUES (?, ?, ?, ?)
                """,
                (now, actor, action, details),
            )
            conn.commit()

    def list_audit_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最新审计日志"""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp, actor, action, details
                FROM audit_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
