"""Lease state helpers and task recovery store for tasks_v2."""
from __future__ import annotations

import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class RecoveryStore:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def _time(self) -> float:
        return float(self.clock())

    def migrate(self) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cols = {row["name"] for row in db.execute("PRAGMA table_info(tasks_v2)").fetchall()}
            if not cols:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks_v2(
                        id TEXT PRIMARY KEY,
                        spec TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'queued',
                        node TEXT NOT NULL DEFAULT '',
                        created REAL NOT NULL DEFAULT 0,
                        updated REAL NOT NULL DEFAULT 0,
                        progress INTEGER NOT NULL DEFAULT 0,
                        log TEXT NOT NULL DEFAULT '',
                        cancelled INTEGER NOT NULL DEFAULT 0,
                        result_hash TEXT,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        lease_token TEXT NOT NULL DEFAULT '',
                        lease_until REAL NOT NULL DEFAULT 0,
                        checkpoint_hash TEXT,
                        recovery_count INTEGER DEFAULT 0
                    );
                    """
                )
            else:
                if "attempt" not in cols:
                    db.execute("ALTER TABLE tasks_v2 ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
                if "lease_token" not in cols:
                    db.execute("ALTER TABLE tasks_v2 ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''")
                if "lease_until" not in cols:
                    db.execute("ALTER TABLE tasks_v2 ADD COLUMN lease_until REAL NOT NULL DEFAULT 0")
                if "checkpoint_hash" not in cols:
                    db.execute("ALTER TABLE tasks_v2 ADD COLUMN checkpoint_hash TEXT")
                if "recovery_count" not in cols:
                    db.execute("ALTER TABLE tasks_v2 ADD COLUMN recovery_count INTEGER DEFAULT 0")

    def get(self, identity: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks_v2 WHERE id = ?", (identity,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def start_lease(
        self, identity: str, node_id: str, seconds: float = 90, now: float | None = None
    ) -> dict[str, Any] | None:
        t = float(now) if now is not None else self._time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, node, attempt FROM tasks_v2 WHERE id = ?", (identity,)
            ).fetchone()
            if not row or row["status"] != "running" or row["node"] != node_id:
                return None
            new_attempt = int(row["attempt"]) + 1
            token = secrets.token_hex(16)
            lease_until = t + seconds
            db.execute(
                "UPDATE tasks_v2 SET attempt = ?, lease_token = ?, lease_until = ?, updated = ? WHERE id = ?",
                (new_attempt, token, lease_until, t, identity),
            )
            return {
                "attempt": new_attempt,
                "lease_token": token,
                "lease_until": lease_until,
            }

    def renew(
        self, identity: str, node_id: str, token: str, seconds: float = 90, now: float | None = None
    ) -> bool:
        if not isinstance(token, str) or not token or not token.isascii():
            return False
        t = float(now) if now is not None else self._time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, node, lease_token FROM tasks_v2 WHERE id = ?", (identity,)
            ).fetchone()
            if not row or row["status"] != "running" or row["node"] != node_id:
                return False
            if not hmac.compare_digest(row["lease_token"], token):
                return False
            lease_until = t + seconds
            db.execute(
                "UPDATE tasks_v2 SET lease_until = ?, updated = ? WHERE id = ?",
                (lease_until, t, identity),
            )
            return True

    def authorized(self, identity: str, node_id: str, token: str) -> bool:
        if not isinstance(token, str) or not token or not token.isascii():
            return False
        with self.connect() as db:
            row = db.execute(
                "SELECT status, node, lease_token FROM tasks_v2 WHERE id = ?", (identity,)
            ).fetchone()
            if not row or row["status"] != "running" or row["node"] != node_id:
                return False
            return hmac.compare_digest(row["lease_token"], token)

    def expire(self, now: float | None = None) -> list[str]:
        t = float(now) if now is not None else self._time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id FROM tasks_v2 WHERE status = 'running' AND lease_until > 0 AND lease_until <= ?",
                (t,),
            ).fetchall()
            expired = [row["id"] for row in rows]
            if expired:
                db.execute(
                    "UPDATE tasks_v2 SET status = 'recovering', updated = ? WHERE status = 'running' AND lease_until > 0 AND lease_until <= ?",
                    (t, t),
                )
            return expired

    def resume(
        self, identity: str, node_id: str, token: str, seconds: float = 90, now: float | None = None
    ) -> bool:
        if not isinstance(token, str) or not token or not token.isascii():
            return False
        t = float(now) if now is not None else self._time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, node, lease_token FROM tasks_v2 WHERE id = ?", (identity,)
            ).fetchone()
            if not row or row["status"] not in ("recovering", "running") or row["node"] != node_id:
                return False
            if not hmac.compare_digest(row["lease_token"], token):
                return False
            lease_until = t + seconds
            db.execute(
                "UPDATE tasks_v2 SET status = 'running', lease_until = ?, updated = ? WHERE id = ?",
                (lease_until, t, identity),
            )
            return True

    def confirm_stopped(
        self, identity: str, node_id: str, token: str, now: float | None = None
    ) -> bool:
        if not isinstance(token, str) or not token or not token.isascii():
            return False
        t = float(now) if now is not None else self._time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, node, lease_token, cancelled, recovery_count FROM tasks_v2 WHERE id = ?",
                (identity,),
            ).fetchone()
            if not row or row["status"] not in ("recovering", "running") or row["node"] != node_id:
                return False
            if not hmac.compare_digest(row["lease_token"], token):
                return False

            target_status = "cancelled" if row["cancelled"] else "queued"
            recovery_count = (row["recovery_count"] or 0) + 1
            db.execute(
                "UPDATE tasks_v2 SET status = ?, node = '', lease_token = '', lease_until = 0.0, recovery_count = ?, updated = ? WHERE id = ?",
                (target_status, recovery_count, t, identity),
            )
            return True
