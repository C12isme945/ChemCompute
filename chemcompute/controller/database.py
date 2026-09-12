"""
Database access layer for ChemCompute Controller.
Uses SQLite for zero-configuration, reliable persistence.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

from chemcompute.common.models import (
    NodeInfo,
    NodeRole,
    NodeState,
    NodeTelemetry,
    SoftwareCatalog,
    JobDetail,
    JobStatus,
    JobRequirements,
    ReleaseChannel,
    ComponentType,
    UpdateStatus,
    ReleaseManifest,
    ReleasePackageInfo,
)


class Database:
    """Thread-safe SQLite database manager for ChemCompute."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Invite codes
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                created_at REAL,
                expires_at REAL,
                max_uses INTEGER,
                uses_count INTEGER,
                is_active INTEGER
            )
            """)

            # Nodes
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                node_name TEXT,
                node_token_hash TEXT,
                ip_address TEXT,
                role TEXT,
                status TEXT,
                telemetry_json TEXT,
                software_json TEXT,
                last_heartbeat REAL,
                registered_at REAL,
                active_job_id TEXT,
                agent_version TEXT DEFAULT '0.1.0',
                channel TEXT DEFAULT 'stable',
                update_status TEXT DEFAULT 'up_to_date'
            )
            """)

            # Ensure columns exist in existing nodes table
            cursor.execute("PRAGMA table_info(nodes)")
            node_cols = [c[1] for c in cursor.fetchall()]
            if "agent_version" not in node_cols:
                cursor.execute("ALTER TABLE nodes ADD COLUMN agent_version TEXT DEFAULT '0.1.0'")
            if "channel" not in node_cols:
                cursor.execute("ALTER TABLE nodes ADD COLUMN channel TEXT DEFAULT 'stable'")
            if "update_status" not in node_cols:
                cursor.execute("ALTER TABLE nodes ADD COLUMN update_status TEXT DEFAULT 'up_to_date'")

            # Releases
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS releases (
                version TEXT,
                component TEXT,
                adapter_name TEXT,
                channel TEXT,
                package_url TEXT,
                sha256 TEXT,
                size_bytes INTEGER,
                filename TEXT,
                minimum_agent_version TEXT,
                changelog TEXT,
                created_at REAL,
                is_active INTEGER,
                PRIMARY KEY (version, component, channel)
            )
            """)

            # Jobs
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT,
                adapter TEXT,
                requirements_json TEXT,
                parameters_json TEXT,
                status TEXT,
                assigned_node_id TEXT,
                progress_pct REAL,
                log_tail TEXT,
                error_message TEXT,
                submitted_at REAL,
                started_at REAL,
                completed_at REAL,
                bundle_path TEXT,
                result_path TEXT,
                priority INTEGER
            )
            """)

            conn.commit()
            conn.close()

    # --- Invite Codes ---
    def create_invite_code(self, code: str, max_uses: int = 10, expires_in_seconds: float = 86400 * 7):
        with self._lock:
            conn = self._get_connection()
            now = time.time()
            conn.execute(
                "INSERT INTO invite_codes VALUES (?, ?, ?, ?, ?, ?)",
                (code, now, now + expires_in_seconds, max_uses, 0, 1)
            )
            conn.commit()
            conn.close()

    def verify_and_consume_invite_code(self, code: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM invite_codes WHERE code = ?", (code,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return False

            now = time.time()
            if row["is_active"] != 1 or row["expires_at"] < now or row["uses_count"] >= row["max_uses"]:
                conn.close()
                return False

            new_count = row["uses_count"] + 1
            is_active = 1 if new_count < row["max_uses"] else 0
            cur.execute("UPDATE invite_codes SET uses_count = ?, is_active = ? WHERE code = ?", (new_count, is_active, code))
            conn.commit()
            conn.close()
            return True

    def list_invite_codes(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM invite_codes ORDER BY created_at DESC")
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_or_create_default_admin_code(self) -> str:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            now = time.time()
            cur.execute("""
            SELECT code FROM invite_codes 
            WHERE is_active = 1 AND expires_at > ? AND uses_count < max_uses 
            ORDER BY created_at DESC LIMIT 1
            """, (now,))
            row = cur.fetchone()
            if row:
                code = row["code"]
                conn.close()
                return code
            from chemcompute.common.security import generate_invite_code
            code = generate_invite_code()
            conn.execute(
                "INSERT INTO invite_codes VALUES (?, ?, ?, ?, ?, ?)",
                (code, now, now + 86400 * 30, 50, 0, 1)
            )
            conn.commit()
            conn.close()
            return code

    # --- Nodes ---
    def register_node(
        self,
        node_id: str,
        node_name: str,
        token_hash: str,
        ip_address: str,
        role: NodeRole,
        software: SoftwareCatalog,
        telemetry: NodeTelemetry,
        agent_version: str = "0.1.0",
        channel: ReleaseChannel = ReleaseChannel.STABLE,
    ):
        with self._lock:
            conn = self._get_connection()
            now = time.time()
            conn.execute("""
            INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id,
                node_name,
                token_hash,
                ip_address,
                role.value,
                NodeState.IDLE.value,
                telemetry.model_dump_json(),
                software.model_dump_json(),
                now,
                now,
                None,
                agent_version,
                channel.value if isinstance(channel, ReleaseChannel) else str(channel),
                UpdateStatus.UP_TO_DATE.value
            ))
            conn.commit()
            conn.close()

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_node(row)

    def verify_node_token(self, node_id: str, token_hash: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT node_token_hash FROM nodes WHERE node_id = ?", (node_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return False
            return row["node_token_hash"] == token_hash

    def update_node_heartbeat(
        self,
        node_id: str,
        status: NodeState,
        telemetry: NodeTelemetry,
        active_job_id: Optional[str] = None,
        agent_version: Optional[str] = None,
        channel: Optional[ReleaseChannel] = None,
        update_status: Optional[UpdateStatus] = None,
    ):
        with self._lock:
            conn = self._get_connection()
            now = time.time()
            # Fetch existing to avoid overwriting None
            cur = conn.cursor()
            cur.execute("SELECT agent_version, channel, update_status FROM nodes WHERE node_id = ?", (node_id,))
            row = cur.fetchone()
            existing_ver = row["agent_version"] if row else "0.1.0"
            existing_chan = row["channel"] if row else "stable"
            existing_stat = row["update_status"] if row else "up_to_date"

            final_ver = agent_version or existing_ver
            final_chan = (channel.value if isinstance(channel, ReleaseChannel) else str(channel)) if channel else existing_chan
            final_stat = (update_status.value if isinstance(update_status, UpdateStatus) else str(update_status)) if update_status else existing_stat

            conn.execute("""
            UPDATE nodes SET status = ?, telemetry_json = ?, last_heartbeat = ?, active_job_id = ?,
                             agent_version = ?, channel = ?, update_status = ?
            WHERE node_id = ?
            """, (status.value, telemetry.model_dump_json(), now, active_job_id, final_ver, final_chan, final_stat, node_id))
            conn.commit()
            conn.close()

    def update_node_channel(self, node_id: str, channel: ReleaseChannel):
        with self._lock:
            conn = self._get_connection()
            chan_val = channel.value if isinstance(channel, ReleaseChannel) else str(channel)
            conn.execute("UPDATE nodes SET channel = ? WHERE node_id = ?", (chan_val, node_id))
            conn.commit()
            conn.close()

    def update_node_update_status(self, node_id: str, update_status: UpdateStatus):
        with self._lock:
            conn = self._get_connection()
            stat_val = update_status.value if isinstance(update_status, UpdateStatus) else str(update_status)
            conn.execute("UPDATE nodes SET update_status = ? WHERE node_id = ?", (stat_val, node_id))
            conn.commit()
            conn.close()

    def list_nodes(self) -> List[NodeInfo]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM nodes ORDER BY registered_at DESC")
            rows = cur.fetchall()
            conn.close()
            return [self._row_to_node(r) for r in rows]

    def _row_to_node(self, row: sqlite3.Row) -> NodeInfo:
        # Check liveness: if heartbeat > 20s ago, mark offline in memory
        now = time.time()
        last_hb = row["last_heartbeat"]
        status = NodeState(row["status"])
        if (now - last_hb) > 20.0 and status != NodeState.OFFLINE:
            status = NodeState.OFFLINE

        keys = row.keys()
        agent_ver = row["agent_version"] if "agent_version" in keys and row["agent_version"] else "0.1.0"
        chan = ReleaseChannel(row["channel"]) if "channel" in keys and row["channel"] else ReleaseChannel.STABLE
        up_stat = UpdateStatus(row["update_status"]) if "update_status" in keys and row["update_status"] else UpdateStatus.UP_TO_DATE

        return NodeInfo(
            node_id=row["node_id"],
            node_name=row["node_name"],
            ip_address=row["ip_address"],
            role=NodeRole(row["role"]),
            status=status,
            telemetry=NodeTelemetry.model_validate_json(row["telemetry_json"]),
            software=SoftwareCatalog.model_validate_json(row["software_json"]),
            last_heartbeat=last_hb,
            registered_at=row["registered_at"],
            active_job_id=row["active_job_id"],
            agent_version=agent_ver,
            channel=chan,
            update_status=up_stat
        )

    # --- Releases & Updates ---
    def create_or_update_release(self, manifest: ReleaseManifest):
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
            INSERT OR REPLACE INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                manifest.version,
                manifest.component.value,
                manifest.adapter_name,
                manifest.channel.value,
                manifest.package.url,
                manifest.package.sha256,
                manifest.package.size_bytes,
                manifest.package.filename,
                manifest.minimum_agent_version,
                manifest.changelog,
                manifest.created_at,
                1 if manifest.is_active else 0
            ))
            conn.commit()
            conn.close()

    def get_release(
        self,
        version: str,
        component: ComponentType = ComponentType.AGENT,
        channel: ReleaseChannel = ReleaseChannel.STABLE,
        adapter_name: Optional[str] = None
    ) -> Optional[ReleaseManifest]:
        comp_val = component.value if isinstance(component, ComponentType) else str(component)
        chan_val = channel.value if isinstance(channel, ReleaseChannel) else str(channel)
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            if adapter_name:
                cur.execute("""
                SELECT * FROM releases WHERE version = ? AND component = ? AND channel = ? AND adapter_name = ?
                """, (version, comp_val, chan_val, adapter_name))
            else:
                cur.execute("""
                SELECT * FROM releases WHERE version = ? AND component = ? AND channel = ?
                """, (version, comp_val, chan_val))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_release(row)

    def list_releases(
        self,
        component: Optional[ComponentType] = None,
        channel: Optional[ReleaseChannel] = None
    ) -> List[ReleaseManifest]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            query = "SELECT * FROM releases WHERE is_active = 1"
            params = []
            if component:
                comp_val = component.value if isinstance(component, ComponentType) else str(component)
                query += " AND component = ?"
                params.append(comp_val)
            if channel:
                chan_val = channel.value if isinstance(channel, ReleaseChannel) else str(channel)
                query += " AND channel = ?"
                params.append(chan_val)
            query += " ORDER BY created_at DESC"
            cur.execute(query, params)
            rows = cur.fetchall()
            conn.close()
            return [self._row_to_release(r) for r in rows]

    def promote_release(
        self,
        version: str,
        to_channel: ReleaseChannel,
        component: ComponentType = ComponentType.AGENT,
        adapter_name: Optional[str] = None
    ) -> bool:
        """Copy/promote an existing release to another channel (e.g. Canary -> Beta -> Stable)."""
        comp_val = component.value if isinstance(component, ComponentType) else str(component)
        to_chan_val = to_channel.value if isinstance(to_channel, ReleaseChannel) else str(to_channel)
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            if adapter_name:
                cur.execute("""
                SELECT * FROM releases WHERE version = ? AND component = ? AND adapter_name = ? LIMIT 1
                """, (version, comp_val, adapter_name))
            else:
                cur.execute("""
                SELECT * FROM releases WHERE version = ? AND component = ? LIMIT 1
                """, (version, comp_val))
            row = cur.fetchone()
            if not row:
                conn.close()
                return False

            now = time.time()
            conn.execute("""
            INSERT OR REPLACE INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["version"],
                row["component"],
                row["adapter_name"],
                to_chan_val,
                row["package_url"],
                row["sha256"],
                row["size_bytes"],
                row["filename"],
                row["minimum_agent_version"],
                row["changelog"],
                now,
                1
            ))
            conn.commit()
            conn.close()
            return True

    def get_latest_release(
        self,
        channel: ReleaseChannel = ReleaseChannel.STABLE,
        component: ComponentType = ComponentType.AGENT,
        adapter_name: Optional[str] = None
    ) -> Optional[ReleaseManifest]:
        comp_val = component.value if isinstance(component, ComponentType) else str(component)
        chan_val = channel.value if isinstance(channel, ReleaseChannel) else str(channel)
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            if adapter_name:
                cur.execute("""
                SELECT * FROM releases WHERE component = ? AND channel = ? AND adapter_name = ? AND is_active = 1
                ORDER BY created_at DESC LIMIT 1
                """, (comp_val, chan_val, adapter_name))
            else:
                cur.execute("""
                SELECT * FROM releases WHERE component = ? AND channel = ? AND is_active = 1
                ORDER BY created_at DESC LIMIT 1
                """, (comp_val, chan_val))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_release(row)

    def _row_to_release(self, row: sqlite3.Row) -> ReleaseManifest:
        return ReleaseManifest(
            version=row["version"],
            component=ComponentType(row["component"]),
            adapter_name=row["adapter_name"],
            channel=ReleaseChannel(row["channel"]),
            package=ReleasePackageInfo(
                url=row["package_url"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                filename=row["filename"]
            ),
            minimum_agent_version=row["minimum_agent_version"] or "0.1.0",
            changelog=row["changelog"] or "",
            created_at=row["created_at"],
            is_active=bool(row["is_active"])
        )

    def seed_official_releases(self):
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM releases WHERE version = '0.3.0'")
            if cur.fetchone()[0] == 0:
                now = time.time()
                for ch in ["stable", "beta", "canary"]:
                    conn.execute("""
                    INSERT OR REPLACE INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        "0.3.0", "agent", None, ch,
                        "https://github.com/C12isme945/ChemCompute/releases/download/v0.3.0/ChemCompute-Setup.exe",
                        "5e75044bf8297a52ae324616a89d1175a877d20037c6f1f35ab39d831f7e169a",
                        18472470, "ChemCompute-Setup.exe", "0.1.0",
                        "ChemCompute v0.3.0: 包含 Windows 安装包、Python 极简控制台、远程断点续算、WSL GROMACS 深度适配及完整校验体系。",
                        now, 1
                    ))
                conn.commit()
            conn.close()

    # --- Jobs ---
    def create_job(
        self,
        job_id: str,
        name: str,
        adapter: str,
        requirements: JobRequirements,
        parameters: Dict[str, Any],
        bundle_path: Optional[str] = None,
        priority: int = 1
    ) -> JobDetail:
        with self._lock:
            conn = self._get_connection()
            now = time.time()
            conn.execute("""
            INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                name,
                adapter,
                requirements.model_dump_json(),
                json.dumps(parameters),
                JobStatus.PENDING.value,
                None,
                0.0,
                "",
                None,
                now,
                None,
                None,
                bundle_path,
                None,
                priority
            ))
            conn.commit()
            conn.close()
            return self.get_job(job_id)

    def get_job(self, job_id: str) -> Optional[JobDetail]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_job(row)

    def list_jobs(self) -> List[JobDetail]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM jobs ORDER BY submitted_at DESC")
            rows = cur.fetchall()
            conn.close()
            return [self._row_to_job(r) for r in rows]

    def get_pending_jobs(self) -> List[JobDetail]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM jobs WHERE status = ? ORDER BY priority DESC, submitted_at ASC
            """, (JobStatus.PENDING.value,))
            rows = cur.fetchall()
            conn.close()
            return [self._row_to_job(r) for r in rows]

    def assign_job(self, job_id: str, node_id: str):
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
            UPDATE jobs SET status = ?, assigned_node_id = ?, started_at = ?
            WHERE job_id = ?
            """, (JobStatus.ASSIGNED.value, node_id, time.time(), job_id))
            conn.commit()
            conn.close()

    def get_assigned_job_for_node(self, node_id: str) -> Optional[JobDetail]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM jobs WHERE assigned_node_id = ? AND status = ?
            ORDER BY submitted_at ASC LIMIT 1
            """, (node_id, JobStatus.ASSIGNED.value))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_job(row)

    def update_job_progress(
        self,
        job_id: str,
        status: JobStatus,
        progress_pct: float,
        log_chunk: str,
        error_message: Optional[str] = None
    ):
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT log_tail FROM jobs WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
            old_log = row["log_tail"] if row else ""
            new_log = (old_log + "\n" + log_chunk).strip()[-4000:]  # Keep last 4000 chars

            completed_at = time.time() if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED] else None

            conn.execute("""
            UPDATE jobs SET status = ?, progress_pct = ?, log_tail = ?, error_message = ?, completed_at = coalesce(?, completed_at)
            WHERE job_id = ?
            """, (status.value, progress_pct, new_log, error_message, completed_at, job_id))
            conn.commit()
            conn.close()

    def set_job_result_path(self, job_id: str, result_path: str):
        with self._lock:
            conn = self._get_connection()
            conn.execute("UPDATE jobs SET result_path = ? WHERE job_id = ?", (result_path, job_id))
            conn.commit()
            conn.close()

    def delete_job(self, job_id: str):
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.commit()
            conn.close()

    def _row_to_job(self, row: sqlite3.Row) -> JobDetail:
        node_name = None
        if row["assigned_node_id"]:
            # Optional lookup
            pass

        return JobDetail(
            job_id=row["job_id"],
            name=row["name"],
            adapter=row["adapter"],
            requirements=JobRequirements.model_validate_json(row["requirements_json"]),
            parameters=json.loads(row["parameters_json"]),
            status=JobStatus(row["status"]),
            assigned_node_id=row["assigned_node_id"],
            assigned_node_name=node_name,
            progress_pct=row["progress_pct"],
            log_tail=row["log_tail"] or "",
            error_message=row["error_message"],
            submitted_at=row["submitted_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            bundle_path=row["bundle_path"],
            result_archive=row["result_path"]
        )
