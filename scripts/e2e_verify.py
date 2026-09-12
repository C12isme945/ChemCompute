"""
End-to-end automated verification script for ChemCompute.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
import httpx

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

CONTROLLER_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent


def run_e2e():
    print("=====================================================")
    print("   ChemCompute End-to-End System Verification        ")
    print("=====================================================")

    # 1. Generate Invite Code
    print("\n[Step 1] Requesting fresh cluster invite code from Controller...")
    with httpx.Client(timeout=10) as client:
        resp = client.post(f"{CONTROLLER_URL}/api/invite-codes", json={"max_uses": 5, "expires_in_days": 1})
        assert resp.status_code == 200, f"Failed: {resp.text}"
        invite_code = resp.json()["code"]
        print(f"  ✓ Generated Invite Code: {invite_code}")

    # 2. Launch Agent using the compiled ChemComputeAgent.exe
    print("\n[Step 2] Launching ChemComputeAgent.exe to enroll this node...")
    agent_exe = ROOT / "dist" / "ChemComputeAgent" / "ChemComputeAgent.exe"
    if not agent_exe.exists():
        agent_exe = sys.executable

    test_config = ROOT / "test_node_config.json"
    if test_config.exists():
        test_config.unlink()

    cmd = [
        str(agent_exe),
        "--controller", CONTROLLER_URL,
        "--invite", invite_code,
        "--name", "Node-3060",
        "--interval", "2.0",
        "--config", str(test_config)
    ]
    agent_proc = subprocess.Popen(cmd, cwd=str(ROOT))

    try:
        # 3. Wait for node to register and appear online
        print("\n[Step 3] Polling Controller for Node-3060 registration...")
        node_registered = False
        for _ in range(15):
            time.sleep(1.0)
            with httpx.Client(timeout=5) as client:
                res = client.get(f"{CONTROLLER_URL}/api/nodes")
                if res.status_code == 200:
                    nodes = res.json()
                    target = [n for n in nodes if n["node_name"] == "Node-3060" and n["status"] == "idle"]
                    if target:
                        node_info = target[0]
                        print(f"  ✓ Node-3060 online! Status: {node_info['status']}")
                        print(f"  ✓ Hardware: CPU={node_info['telemetry']['cpu']['model']} | RAM={node_info['telemetry']['ram']['total_mb']}MB")
                        if node_info['telemetry']['gpu']:
                            print(f"  ✓ GPU: {node_info['telemetry']['gpu']['name']} (VRAM: {node_info['telemetry']['gpu']['memory_total_mb']}MB)")
                        if node_info['software']['gromacs']:
                            print(f"  ✓ GROMACS: v{node_info['software']['gromacs']['version']} (WSL={node_info['software']['gromacs']['is_wsl']})")
                        node_registered = True
                        break

        assert node_registered, "Node-3060 failed to register within timeout."

        # 4. Submit GROMACS Calculation Job
        print("\n[Step 4] Submitting GROMACS calculation job (HA-WaterBox-MD)...")
        bundle_file = ROOT / "examples" / "test_gromacs_job" / "test_bundle.zip"
        assert bundle_file.exists(), "test_bundle.zip not found."

        job_meta = {
            "name": "HA-WaterBox-MD-Verify",
            "adapter": "gromacs",
            "requirements": {
                "software": "gromacs",
                "require_gpu": False
            },
            "parameters": {
                "nsteps": 100,
                "use_gpu": False
            },
            "priority": 10
        }

        with httpx.Client(timeout=20) as client:
            with open(bundle_file, "rb") as f:
                files = {"bundle": (bundle_file.name, f, "application/zip")}
                data = {"metadata": json.dumps(job_meta)}
                resp = client.post(f"{CONTROLLER_URL}/api/jobs", files=files, data=data)
                assert resp.status_code == 200, f"Submit failed: {resp.text}"
                job_data = resp.json()
                job_id = job_data["job_id"]
                print(f"  ✓ Job submitted! ID: {job_id}")

        # 5. Monitor execution and progress
        print("\n[Step 5] Monitoring job dispatch, live mdrun step tracking, and execution...")
        completed = False
        for i in range(30):
            time.sleep(1.5)
            with httpx.Client(timeout=5) as client:
                res = client.get(f"{CONTROLLER_URL}/api/jobs/{job_id}")
                if res.status_code == 200:
                    j = res.json()
                    status = j["status"]
                    pct = j["progress_pct"]
                    tail = (j["log_tail"] or "").strip().split("\n")[-1]
                    print(f"  [Progress {pct:5.1f}%] Status: {status:10s} | {tail[:60]}")
                    if status == "completed":
                        completed = True
                        break
                    elif status == "failed":
                        print(f"  ✗ Job failed: {j.get('error_message')}")
                        break

        assert completed, "Job did not complete successfully within time."

        # 6. Verify and Download Results
        print("\n[Step 6] Downloading and verifying calculation results archive...")
        with httpx.Client(timeout=10) as client:
            res = client.get(f"{CONTROLLER_URL}/api/jobs/{job_id}/results")
            assert res.status_code == 200, f"Failed to download results: {res.status_code}"
            result_zip = ROOT / "test_downloaded_results.zip"
            result_zip.write_bytes(res.content)
            print(f"  ✓ Downloaded {result_zip.name} ({len(res.content)} bytes)")

            import zipfile
            with zipfile.ZipFile(result_zip, "r") as zf:
                namelist = zf.namelist()
                print(f"  ✓ Result archive contains {len(namelist)} artifacts: {namelist}")
                assert "run.gro" in namelist or "conf.gro" in namelist
                assert "run.log" in namelist

        print("\n=====================================================")
        print("  ✓ ALL END-TO-END VERIFICATION CHECKS PASSED!        ")
        print("=====================================================")

    finally:
        print("\nShutting down test agent process...")
        agent_proc.terminate()
        try:
            agent_proc.wait(timeout=3)
        except Exception:
            agent_proc.kill()
        if test_config.exists():
            test_config.unlink()


if __name__ == "__main__":
    run_e2e()
