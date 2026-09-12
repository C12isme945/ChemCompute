"""
End-to-end automated verification script for ChemCompute Hot Update & Canary System.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
import httpx

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chemcompute.controller.app import app
from chemcompute.controller.database import Database
from chemcompute.agent.daemon import AgentDaemon
from chemcompute.agent.plugin_manager import PluginManager
from chemcompute.common.models import ReleaseChannel, NodeState
from fastapi.testclient import TestClient


def run_e2e_update_verification():
    print("==================================================================")
    print("   ChemCompute Multi-Tier Hot Update & Canary E2E Verification    ")
    print("==================================================================")

    with tempfile.TemporaryDirectory() as td:
        test_dir = Path(td)
        db_path = test_dir / "controller.db"
        storage_path = test_dir / "storage"
        storage_path.mkdir(parents=True, exist_ok=True)

        app.state.db = Database(db_path)
        app.state.storage_root = storage_path

        with TestClient(app) as client:
            # 1. Cluster invite code and enrollment
            print("\n[Step 1] Creating invite code and enrolling Canary & Stable nodes...")
            inv_res = client.post("/api/invite-codes", json={"max_uses": 10, "expires_in_days": 1})
            assert inv_res.status_code == 200
            invite_code = inv_res.json()["code"]
            print(f"  ✓ Generated invite code: {invite_code}")

            # Enroll Canary node (v0.1.0)
            res_canary = client.post("/api/nodes/enroll", json={
                "invite_code": invite_code,
                "node_name": "Node-RTX3060-Canary",
                "agent_version": "0.1.0",
                "channel": "canary"
            })
            assert res_canary.status_code == 200
            canary_id = res_canary.json()["node_id"]
            print(f"  ✓ Canary Node enrolled: {canary_id} (Channel: canary, Version: 0.1.0)")

            # Enroll Stable node (v0.1.0)
            res_stable = client.post("/api/nodes/enroll", json={
                "invite_code": invite_code,
                "node_name": "Node-Lab01-Stable",
                "agent_version": "0.1.0",
                "channel": "stable"
            })
            assert res_stable.status_code == 200
            stable_id = res_stable.json()["node_id"]
            print(f"  ✓ Stable Node enrolled: {stable_id} (Channel: stable, Version: 0.1.0)")

            # 2. Check update before release
            print("\n[Step 2] Checking for updates (expecting none)...")
            chk_canary_0 = client.get(f"/api/v1/update/check?node_id={canary_id}&agent_version=0.1.0")
            assert chk_canary_0.json()["update_available"] is False
            print("  ✓ No updates available as expected.")

            # 3. Publish Agent v0.2.0 Package to Canary Channel
            print("\n[Step 3] Publishing Agent v0.2.0 Package to Canary channel...")
            pkg_zip = test_dir / "ChemComputeAgent-0.2.0.zip"
            with zipfile.ZipFile(pkg_zip, "w") as zf:
                zf.writestr("version.txt", "0.2.0")
                zf.writestr("release_notes.txt", "Support dual-process hot update and dynamic adapters.")

            h = hashlib.sha256()
            with open(pkg_zip, "rb") as f:
                h.update(f.read())
            pkg_sha = h.hexdigest()

            with open(pkg_zip, "rb") as f:
                meta = {
                    "version": "0.2.0",
                    "component": "agent",
                    "channel": "canary",
                    "changelog": "Tier-1 dual process updater & task-aware deferral.",
                    "minimum_agent_version": "0.1.0"
                }
                upload_res = client.post(
                    "/api/v1/releases",
                    data={"metadata": json.dumps(meta)},
                    files={"package": (pkg_zip.name, f, "application/zip")}
                )
                assert upload_res.status_code == 200
                rel_data = upload_res.json()
                print(f"  ✓ Release v0.2.0 registered: Channel={rel_data['channel']}, SHA256={rel_data['package']['sha256'][:12]}...")

            # 4. Verify Canary node detects v0.2.0
            print("\n[Step 4] Verifying Canary node detects v0.2.0 while Stable node remains unchanged...")
            chk_canary_1 = client.get(f"/api/v1/update/check?node_id={canary_id}&agent_version=0.1.0")
            assert chk_canary_1.json()["update_available"] is True
            assert chk_canary_1.json()["version"] == "0.2.0"
            print(f"  ✓ Canary node received update notice: Target v{chk_canary_1.json()['version']}")

            # Verify Stable node does NOT see update
            chk_stable_0 = client.get(f"/api/v1/update/check?node_id={stable_id}&agent_version=0.1.0")
            assert chk_stable_0.json()["update_available"] is False
            print("  ✓ Stable node correctly shielded from Canary release.")

            # 5. Phased rollout promotion: Canary -> Beta -> Stable
            print("\n[Step 5] Promoting release through phased rollout: Canary -> Beta -> Stable...")
            promo_beta = client.post("/api/v1/releases/0.2.0/promote", json={"to_channel": "beta"})
            assert promo_beta.status_code == 200
            print("  ✓ v0.2.0 promoted to Beta.")

            promo_stable = client.post("/api/v1/releases/0.2.0/promote", json={"to_channel": "stable"})
            assert promo_stable.status_code == 200
            print("  ✓ v0.2.0 promoted to Stable.")

            chk_stable_1 = client.get(f"/api/v1/update/check?node_id={stable_id}&agent_version=0.1.0")
            assert chk_stable_1.json()["update_available"] is True
            assert chk_stable_1.json()["version"] == "0.2.0"
            print(f"  ✓ Stable node now detects v{chk_stable_1.json()['version']} update ready to download!")

            # 6. Adapter Hot-Reload Verification (Zero-Restart)
            print("\n[Step 6] Verifying Adapter Dynamic Hot-Reloading (PluginManager)...")
            adapters_dir = test_dir / "adapters"
            pm = PluginManager(adapters_dir=adapters_dir)
            assert pm.get_adapter("orca") is None

            # Create dynamic orca plugin package
            orca_code = """
from pathlib import Path
from typing import Dict, Any, List
from chemcompute.adapters.base import BaseAdapter

class OrcaLiveAdapter(BaseAdapter):
    def __init__(self):
        super().__init__("orca")
    def detect(self) -> Dict[str, Any]:
        return {"available": True, "version": "5.0.4"}
    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        return True
    def run(self, workspace_dir: Path, parameters: Dict[str, Any], progress_callback=None) -> bool:
        return True
    def collect(self, workspace_dir: Path) -> List[Path]:
        return []
"""
            orca_zip = test_dir / "orca-adapter-5.0.4.zip"
            with zipfile.ZipFile(orca_zip, "w") as zf:
                zf.writestr("adapter.py", orca_code)

            h_ad = hashlib.sha256()
            with open(orca_zip, "rb") as f:
                h_ad.update(f.read())

            ok = pm.hot_reload_adapter("orca", orca_zip, expected_sha256=h_ad.hexdigest())
            assert ok is True
            orca_inst = pm.get_adapter("orca")
            assert orca_inst is not None
            assert orca_inst.detect()["version"] == "5.0.4"
            print(f"  ✓ Adapter 'orca' hot-reloaded dynamically into runtime without process restart!")

            # 7. Task-Awareness Verification
            print("\n[Step 7] Verifying Task-Aware Update Deferral during active job...")
            daemon = AgentDaemon(
                controller_url="http://127.0.0.1:8000",
                config_path=test_dir / "node.json",
                workspace_dir=test_dir / "ws",
                node_name="Node-Active",
                agent_version="0.1.0",
                channel=ReleaseChannel.STABLE,
                agent_root_dir=test_dir
            )
            daemon.node_id = canary_id
            daemon.node_token = "dummy"

            # Simulate GROMACS MD running
            daemon.status = NodeState.BUSY
            daemon.active_job_id = "job-mdrun-48h"
            daemon.active_job_adapter = "gromacs"

            # Receive update push
            daemon.update_client.handle_event({
                "type": "agent.update_available",
                "version": "0.2.0",
                "url": "/api/v1/releases/0.2.0/download",
                "sha256": pkg_sha
            })

            assert daemon.pending_update is not None, "Update should be deferred!"
            print(f"  ✓ GROMACS job 'job-mdrun-48h' active: Update safely deferred to pending queue.")

            # Finish job
            daemon.status = NodeState.IDLE
            daemon.active_job_id = None
            print("  ✓ GROMACS job completed: Ready for updater execution.")

            print("\n==================================================================")
            print("  ✓ ALL HOT UPDATE & CANARY E2E VERIFICATIONS PASSED!             ")
            print("==================================================================")


if __name__ == "__main__":
    run_e2e_update_verification()
