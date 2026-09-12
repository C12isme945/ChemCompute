"""Fail-closed, transactional pause of task intake before a local upgrade."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import psutil
from fastapi import HTTPException

from chemcompute.common.config import ControllerConfig, NodeConfig, load_yaml_config


class MaintenanceBusy(HTTPException):
    def __init__(self, detail='正在准备更新，暂时不能提交或领取计算任务。'):
        super().__init__(status_code=503, detail=detail)


def check_intake(connection):
    """Call inside the SAME BEGIN IMMEDIATE transaction as create/claim."""
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='update_maintenance'").fetchone()
    if exists and connection.execute('SELECT 1 FROM update_maintenance WHERE expires_at>?', (time.time(),)).fetchone():
        raise MaintenanceBusy()


def _database(home):
    config_path = home / 'config/controller.yaml'
    if not config_path.is_file():
        raise MaintenanceBusy('缺少主控配置，无法安全检查任务。')
    config = load_yaml_config(config_path, ControllerConfig)
    path = Path(config.db_path)
    return path if path.is_absolute() else home / path


def _check_spools(home, role):
    roots = {home / 'data/workspace'}
    if role == 'both':
        path = home / 'config/node.yaml'
        if not path.is_file():
            raise MaintenanceBusy('缺少本机节点配置，无法检查计算状态。')
        node = load_yaml_config(path, NodeConfig)
        ctrl = load_yaml_config(home / 'config/controller.yaml', ControllerConfig)
        url = urlparse(node.controller_url)
        if url.hostname not in {'127.0.0.1', 'localhost', '::1'} or (url.port or (443 if url.scheme == 'https' else 80)) != ctrl.port:
            raise MaintenanceBusy('本机节点连接远程主控，请停止远程任务后手动升级。')
        root = Path(node.workspace_dir)
        roots.add(root if root.is_absolute() else home / root)
    for root in roots:
        if not root.exists():
            continue
        for task_dir in root.glob('task-*'):
            if not task_dir.is_dir():
                continue
            state = json.loads((task_dir / 'worker-state.json').read_text('utf-8'))
            if state.get('done') is not True:
                raise MaintenanceBusy('本机仍有未完成或等待回传的计算，请恢复并完成后更新。')
            process_file = task_dir / 'process.json'
            if process_file.exists():
                record = json.loads(process_file.read_text('utf-8'))
                if record.get('finished') is not True:
                    raise MaintenanceBusy('本机计算进程尚未确认退出，请检查任务后更新。')


def prepare_install(runtime_home=None, *, ttl_seconds=900):
    """Reserve idle local controller/both runtime; caller then stops its backend.

    Returned ticket is opaque to callers except its expiry. It must remain outside
    the release package. Node-only upgrades are deliberately unsupported.
    """
    home = Path(runtime_home or Path.cwd()).resolve()
    try:
        role = json.loads((home / 'role.json').read_text('utf-8'))['role']
        if role not in {'controller', 'both'}:
            raise MaintenanceBusy('计算节点模式暂不支持自动升级，请确认远程任务结束后手动更新。')
        path = _database(home).resolve()
        if not path.is_file():
            raise MaintenanceBusy('主控数据库不存在，无法确认任务已经停止。')
        if not 30 <= ttl_seconds <= 3600:
            raise ValueError('Maintenance TTL must be between 30 and 3600 seconds')
        with sqlite3.connect(path, timeout=15) as conn:
            conn.execute('BEGIN IMMEDIATE')
            check_intake(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'jobs' not in tables or 'nodes' not in tables:
                raise MaintenanceBusy('数据库结构未知，无法安全升级。')
            node_columns = {row[1] for row in conn.execute('PRAGMA table_info(nodes)')}
            job_columns = {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
            if not {'name', 'token_hash', 'last_seen'} <= node_columns or not {'subcommand', 'arguments_json', 'created_at'} <= job_columns:
                raise MaintenanceBusy('数据库版本不兼容，已取消自动升级。')
            # Read every row through SQL, never through paginated API lists.
            if conn.execute("SELECT 1 FROM jobs WHERE status IS NULL OR status NOT IN ('completed','failed','cancelled','timeout') LIMIT 1").fetchone():
                raise MaintenanceBusy('仍有排队或运行中的作业，请完成或取消后更新。')
            if 'tasks_v2' in tables and conn.execute("SELECT 1 FROM tasks_v2 WHERE status IS NULL OR status NOT IN ('completed','failed','cancelled') LIMIT 1").fetchone():
                raise MaintenanceBusy('仍有排队、运行或恢复中的计算包任务。')
            _check_spools(home, role)
            conn.execute('CREATE TABLE IF NOT EXISTS update_maintenance (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL, expires_at REAL NOT NULL, owner_pid INTEGER NOT NULL, owner_created REAL NOT NULL)')
            token = secrets.token_urlsafe(32)
            expires = time.time() + ttl_seconds
            conn.execute('INSERT OR REPLACE INTO update_maintenance VALUES(1,?,?,?,?)', (token, expires, os.getpid(), psutil.Process().create_time()))
        return {'token': token, 'db_path': str(path), 'expires_at': expires}
    except MaintenanceBusy:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, psutil.Error) as exc:
        raise MaintenanceBusy('无法确认本机计算状态，已取消自动升级。') from exc


def finish_install(ticket):
    """Release only this reservation; stale tickets cannot clear a later gate."""
    path = Path(ticket['db_path'])
    if not path.is_file():
        return False
    with sqlite3.connect(path, timeout=15) as conn:
        conn.execute('BEGIN IMMEDIATE')
        return conn.execute('DELETE FROM update_maintenance WHERE id=1 AND token=?', (ticket['token'],)).rowcount == 1


def local_intake_paused(runtime_home=None):
    """Additional local polling guard; controller transaction remains authoritative."""
    home = Path(runtime_home or Path.cwd()).resolve()
    if not (home / 'role.json').exists():
        return False  # CLI-only workers are not eligible for prepare_install.
    try:
        role = json.loads((home / 'role.json').read_text('utf-8'))['role']
        if role == 'node':
            return False
        path = _database(home)
        if not path.is_file():
            return True
        with sqlite3.connect(path, timeout=2) as conn:
            check_intake(conn)
        return False
    except (MaintenanceBusy, OSError, ValueError, KeyError, sqlite3.Error):
        return True
