"""
Update Client for ChemCompute Agent.
Handles WebSocket push events, hourly fallback polling, Task-Aware update deferrals,
and invoking the dual-process updater.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any
import httpx
import websockets

from chemcompute.common.models import NodeState, ReleaseChannel


class UpdateClient:
    """Listens for controller update pushes and triggers safe task-aware updates."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.controller_url = daemon.controller_url.rstrip("/")
        self.node_id = daemon.node_id
        self.poll_interval = 3600.0  # 1 hour fallback
        self._running = False
        self._ws_task = None
        self._thread = None

    def start(self):
        """Start background listener thread for WebSocket + polling."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run_loop(self):
        """Thread worker running async event loop."""
        asyncio.run(self._async_main())

    async def _async_main(self):
        # Run WebSocket listener and periodic poller concurrently
        ws_coro = self._ws_listener()
        poll_coro = self._periodic_poller()
        await asyncio.gather(ws_coro, poll_coro)

    async def _ws_listener(self):
        ws_url = self.controller_url.replace("http://", "ws://").replace("https://", "wss://")
        endpoint = f"{ws_url}/ws/nodes/{self.daemon.node_id}"

        while self._running:
            try:
                async with websockets.connect(endpoint) as ws:
                    print(f"[UpdateClient] Connected to Controller WebSocket gateway.")
                    while self._running:
                        msg_raw = await ws.recv()
                        try:
                            msg = json.loads(msg_raw)
                            self.handle_event(msg)
                        except Exception as e:
                            print(f"[UpdateClient] Error processing event: {e}")
            except Exception:
                # Connection dropped, retry after 5 seconds
                await asyncio.sleep(5.0)

    async def _periodic_poller(self):
        """Fallback polling loop every 1 hour (or faster if configured)."""
        while self._running:
            await asyncio.sleep(self.poll_interval)
            try:
                self.check_update_now()
            except Exception:
                pass

    def check_update_now(self) -> Optional[Dict[str, Any]]:
        """Query /api/v1/update/check manually."""
        if not self.daemon.node_id:
            return None

        channel = self.daemon.channel.value if isinstance(self.daemon.channel, ReleaseChannel) else str(self.daemon.channel)
        url = f"{self.controller_url}/api/v1/update/check"
        params = {
            "node_id": self.daemon.node_id,
            "agent_version": self.daemon.agent_version,
            "channel": channel,
            "platform": "windows-x64"
        }

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("update_available"):
                        self.handle_event({
                            "type": "agent.update_available",
                            "version": data["version"],
                            "url": data["url"],
                            "sha256": data["sha256"],
                            "changelog": data.get("changelog", "")
                        })
                        return data
        except Exception:
            pass
        return None

    def handle_event(self, event: Dict[str, Any]):
        """Handle incoming controller notifications."""
        evt_type = event.get("type")

        # 1. Agent Core Update Available
        if evt_type == "agent.update_available":
            version = event.get("version")
            print(f"\n[UpdateClient] Notification: New Agent version v{version} is available!")

            # Task Awareness Check
            if self.daemon.status == NodeState.BUSY or self.daemon.active_job_id:
                print(f"[UpdateClient] [Task-Aware] Node is currently computing job '{self.daemon.active_job_id}'.")
                print(f"[UpdateClient] [Task-Aware] Update to v{version} is deferred until current job completes.")
                self.daemon.pending_update = event
            else:
                self.execute_agent_update(event)

        # 2. Adapter Hot-Update Available
        elif evt_type == "adapter.update_available":
            adapter_name = event.get("adapter") or event.get("adapter_name")
            version = event.get("version")
            print(f"\n[UpdateClient] Notification: Adapter '{adapter_name}' v{version} update available!")

            # Check if current job uses this adapter
            if self.daemon.active_job_adapter == adapter_name and self.daemon.status == NodeState.BUSY:
                print(f"[UpdateClient] Adapter '{adapter_name}' is currently active. Deferring reload.")
                self.daemon.pending_adapter_update = event
            else:
                self.execute_adapter_update(event)

        # 3. Dynamic Configuration Push
        elif evt_type == "config.push":
            config_patch = event.get("config", {})
            print(f"\n[UpdateClient] Notification: Received configuration push: {config_patch}")
            self.daemon.config_manager.apply_patch(config_patch)

    def execute_adapter_update(self, event: Dict[str, Any]):
        """Download adapter package and hot-reload via PluginManager without restarting agent."""
        adapter_name = event.get("adapter") or event.get("adapter_name")
        pkg_url = event.get("url", "")
        if pkg_url.startswith("/"):
            pkg_url = f"{self.controller_url}{pkg_url}"

        expected_sha256 = event.get("sha256", "")
        temp_zip = self.daemon.workspace_dir / f"adapter_{adapter_name}_update.zip"

        try:
            print(f"[UpdateClient] Downloading adapter package: {pkg_url}...")
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                resp = client.get(pkg_url)
                if resp.status_code == 200:
                    temp_zip.write_bytes(resp.content)
                else:
                    print(f"[UpdateClient] Failed to download adapter: HTTP {resp.status_code}")
                    return

            success = self.daemon.plugin_manager.hot_reload_adapter(
                name=adapter_name,
                zip_path=temp_zip,
                expected_sha256=expected_sha256
            )
            temp_zip.unlink(missing_ok=True)

            if success:
                print(f"[UpdateClient] Adapter '{adapter_name}' hot-reloaded successfully!")
                self.daemon.send_heartbeat()
        except Exception as ex:
            print(f"[UpdateClient] Error during adapter hot-reload: {ex}")
            temp_zip.unlink(missing_ok=True)

    def execute_agent_update(self, event: Dict[str, Any]):
        """Spawn independent Updater process and shut down daemon gracefully."""
        version = event.get("version")
        pkg_url = event.get("url", "")
        if pkg_url.startswith("/"):
            pkg_url = f"{self.controller_url}{pkg_url}"
        expected_sha256 = event.get("sha256", "")

        print(f"[UpdateClient] Preparing dual-process update to v{version}...")

        # Locate root agent dir (where current/ previous/ live)
        agent_dir = self.daemon.agent_root_dir
        my_pid = os.getpid()

        # Build updater command
        updater_script = Path(__file__).resolve().parent.parent / "updater" / "updater.py"
        run_updater_script = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_updater.py"

        script_to_run = run_updater_script if run_updater_script.exists() else updater_script
        cmd = [
            sys.executable,
            str(script_to_run),
            "--agent-dir", str(agent_dir),
            "--package", pkg_url,
            "--sha256", expected_sha256,
            "--version", str(version),
            "--agent-pid", str(my_pid),
            "--launch-cmd", sys.executable, str(sys.argv[0]), *sys.argv[1:]
        ]

        print(f"[UpdateClient] Launching standalone updater: {' '.join(cmd)}")
        subprocess.Popen(cmd, cwd=str(agent_dir))

        # Gracefully stop daemon loop so file locks are freed
        self.daemon.stop()
