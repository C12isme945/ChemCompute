"""
ChemCompute Agent Daemon.
Runs continuously on compute nodes, reports telemetry, executes assigned jobs,
and supports multi-tier hot updates (core updater, adapter plugins, and dynamic config).
"""

import json
import os
import platform
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any
import httpx

from chemcompute.agent.hardware import probe_hardware
from chemcompute.agent.software_scanner import scan_software
from chemcompute.agent.runner import JobRunner
from chemcompute.agent.config_manager import ConfigManager
from chemcompute.agent.plugin_manager import PluginManager
from chemcompute.agent.update_client import UpdateClient
from chemcompute.common.models import (
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    NodeRole,
    NodeState,
    ReleaseChannel,
    UpdateStatus,
)


class AgentDaemon:
    """Continuous worker agent service for ChemCompute cluster."""

    def __init__(
        self,
        controller_url: str = "http://127.0.0.1:8000",
        config_path: Optional[Path] = None,
        workspace_dir: Optional[Path] = None,
        node_name: Optional[str] = None,
        heartbeat_interval: float = 5.0,
        agent_version: str = "0.1.0",
        channel: ReleaseChannel = ReleaseChannel.STABLE,
        agent_root_dir: Optional[Path] = None
    ):
        self.controller_url = controller_url.rstrip("/")
        self.config_path = config_path or (Path.cwd() / "node_config.json")
        self.workspace_dir = workspace_dir or (Path.cwd() / "workspace")
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.agent_root_dir = agent_root_dir or Path.cwd()
        self.node_name = node_name or socket.gethostname()
        self.heartbeat_interval = heartbeat_interval
        self.agent_version = agent_version
        self.channel = channel
        self.update_status = UpdateStatus.UP_TO_DATE

        self.node_id: Optional[str] = None
        self.node_token: Optional[str] = None
        self.status = NodeState.IDLE
        self.active_job_id: Optional[str] = None
        self.active_job_adapter: Optional[str] = None
        self.pending_update: Optional[Dict[str, Any]] = None
        self.pending_adapter_update: Optional[Dict[str, Any]] = None
        self._running = False

        # Load local credential state
        self._load_config()

        # Component managers
        yaml_config_path = self.agent_root_dir / "config" / "node.yaml"
        self.config_manager = ConfigManager(yaml_config_path if yaml_config_path.parent.exists() else self.config_path)
        if hasattr(self.config_manager.config, "update_channel"):
            self.channel = self.config_manager.config.update_channel

        self.plugin_manager = PluginManager(self.agent_root_dir / "adapters")
        self.update_client = UpdateClient(self)

    def _load_config(self):
        """Load stored node credentials if present."""
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.node_id = data.get("node_id")
                self.node_token = data.get("node_token")
                self.node_name = data.get("node_name", self.node_name)
                self.controller_url = data.get("controller_url", self.controller_url).rstrip("/")
                if data.get("channel"):
                    self.channel = ReleaseChannel(data["channel"])
                if data.get("agent_version"):
                    self.agent_version = data["agent_version"]
            except Exception:
                pass

    def _save_config(self):
        """Save node credentials to local JSON."""
        data = {
            "node_id": self.node_id,
            "node_token": self.node_token,
            "node_name": self.node_name,
            "controller_url": self.controller_url,
            "channel": self.channel.value if isinstance(self.channel, ReleaseChannel) else str(self.channel),
            "agent_version": self.agent_version
        }
        self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def enroll(self, invite_code: str) -> bool:
        """Enroll this machine into ChemCompute cluster with an invite code."""
        print(f"[ChemCompute Agent] Enrolling node '{self.node_name}' (v{self.agent_version}, channel: {self.channel}) with code: {invite_code}...")
        telemetry = probe_hardware()
        software = scan_software()

        req = EnrollmentRequest(
            invite_code=invite_code,
            node_name=self.node_name,
            role=NodeRole.WORKER,
            ip_address=socket.gethostbyname(socket.gethostname()),
            software=software,
            telemetry=telemetry,
            agent_version=self.agent_version,
            channel=self.channel
        )

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.controller_url}/api/nodes/enroll",
                    json=req.model_dump()
                )
                if resp.status_code == 200:
                    data = resp.json()
                    res = EnrollmentResponse(**data)
                    if res.success:
                        self.node_id = res.node_id
                        self.node_token = res.node_token
                        if res.assigned_channel:
                            self.channel = res.assigned_channel
                        self._save_config()
                        print(f"[OK] Node enrolled successfully! Assigned ID: {self.node_id}")
                        return True
                    else:
                        print(f"[FAIL] Enrollment rejected: {res.message}")
                        return False
                else:
                    print(f"[FAIL] Enrollment failed with HTTP {resp.status_code}: {resp.text}")
                    return False
        except Exception as ex:
            print(f"[FAIL] Network error connecting to controller: {str(ex)}")
            return False

    def send_heartbeat(self) -> Optional[HeartbeatResponse]:
        """Report live telemetry, channel, version, and node status to controller."""
        if not self.node_id or not self.node_token:
            return None

        telemetry = probe_hardware()
        req = HeartbeatRequest(
            node_id=self.node_id,
            node_token=self.node_token,
            status=self.status,
            telemetry=telemetry,
            active_job_id=self.active_job_id,
            agent_version=self.agent_version,
            channel=self.channel,
            update_status=self.update_status
        )

        try:
            with httpx.Client(timeout=5) as client:
                resp = client.post(
                    f"{self.controller_url}/api/nodes/heartbeat",
                    json=req.model_dump(),
                    headers={"Authorization": f"Bearer {self.node_token}", "X-Node-ID": self.node_id}
                )
                if resp.status_code == 200:
                    hb_res = HeartbeatResponse(**resp.json())
                    # Handle piggybacked update notification
                    if hb_res.update_available and hb_res.update_available.update_available:
                        up_data = hb_res.update_available
                        self.update_client.handle_event({
                            "type": "agent.update_available",
                            "version": up_data.version,
                            "url": up_data.url,
                            "sha256": up_data.sha256,
                            "changelog": up_data.changelog
                        })
                    if hb_res.config_patch:
                        self.config_manager.apply_patch(hb_res.config_patch)
                    return hb_res
        except Exception:
            pass
        return None

    def poll_for_job(self) -> Optional[dict]:
        """Poll controller to check if a job is assigned to this node."""
        if not self.node_id or not self.node_token:
            return None

        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(
                    f"{self.controller_url}/api/agent/jobs/poll",
                    headers={"Authorization": f"Bearer {self.node_token}", "X-Node-ID": self.node_id}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data and data.get("job_id"):
                        return data
        except Exception:
            pass
        return None

    def run_loop(self):
        """Main agent loop with task-aware update checking and background client."""
        if not self.node_id or not self.node_token:
            print("[ChemCompute Agent] Node is not enrolled yet. Please provide an invite code.")
            return

        self._running = True
        print(f"[ChemCompute Agent] Running daemon for {self.node_name} (ID: {self.node_id})")
        print(f"Controller: {self.controller_url} | Version: {self.agent_version} | Channel: {self.channel.value}")

        # Start WebSocket + polling update client
        self.update_client.start()

        runner = JobRunner(
            workspace_root=self.workspace_dir,
            controller_url=self.controller_url,
            node_id=self.node_id,
            node_token=self.node_token,
            plugin_manager=self.plugin_manager
        )

        while self._running:
            try:
                # 1. Send heartbeat
                self.send_heartbeat()

                # 2. Check if idle, poll for job
                if self.status == NodeState.IDLE:
                    job = self.poll_for_job()
                    if job:
                        self.status = NodeState.BUSY
                        self.active_job_id = job["job_id"]
                        self.active_job_adapter = job.get("adapter", "gromacs").lower()
                        print(f"-> Starting assigned job: {self.active_job_id} ({job.get('name')})")
                        try:
                            runner.run_job(job)
                        finally:
                            self.status = NodeState.IDLE
                            self.active_job_id = None
                            self.active_job_adapter = None
                            self.send_heartbeat()

                            # Task-Awareness: Trigger deferred updates now that calculation has finished safely
                            if self.pending_adapter_update:
                                pending_ad = self.pending_adapter_update
                                self.pending_adapter_update = None
                                print(f"[ChemCompute Agent] [Task-Aware] Job completed. Executing deferred adapter reload...")
                                self.update_client.execute_adapter_update(pending_ad)

                            if self.pending_update:
                                pending = self.pending_update
                                self.pending_update = None
                                print(f"[ChemCompute Agent] [Task-Aware] Job completed. Executing deferred Agent core update...")
                                self.update_client.execute_agent_update(pending)
                                break

                time.sleep(self.heartbeat_interval)
            except KeyboardInterrupt:
                print("\n[ChemCompute Agent] Stopping agent daemon...")
                self._running = False
                break
            except Exception as ex:
                time.sleep(self.heartbeat_interval)

        self.update_client.stop()

    def stop(self):
        self._running = False
        if hasattr(self, "update_client"):
            self.update_client.stop()
