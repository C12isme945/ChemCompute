"""
API integration tests for ChemCompute Controller.
"""

from fastapi.testclient import TestClient
from chemcompute.controller.app import app
from chemcompute.common.models import (
    NodeRole,
    SoftwareCatalog,
    SoftwareGromacs,
    NodeTelemetry,
    HardwareCPU,
    HardwareRAM,
    JobStatus
)


def test_controller_api_lifecycle():
    with TestClient(app) as client:
        # 1. Cluster stats should be available
        res = client.get("/api/cluster/stats")
        assert res.status_code == 200
        data = res.json()
        assert "total_nodes" in data

        # 2. Generate invite code
        res = client.post("/api/invite-codes", json={"max_uses": 5, "expires_in_days": 1})
        assert res.status_code == 200
        code_data = res.json()
        code = code_data["code"]
        assert code.startswith("CC-")

        # 3. Enroll node
        enroll_req = {
            "invite_code": code,
            "node_name": "Test-Worker-1",
            "role": "worker",
            "ip_address": "127.0.0.1",
            "software": SoftwareCatalog(
                gromacs=SoftwareGromacs(available=True, version="2023.3", is_wsl=True)
            ).model_dump(),
            "telemetry": NodeTelemetry(
                cpu=HardwareCPU(logical_cores=8, utilization_pct=10.0),
                ram=HardwareRAM(total_mb=16384, available_mb=12000, used_pct=25.0)
            ).model_dump()
        }
        res = client.post("/api/nodes/enroll", json=enroll_req)
        assert res.status_code == 200
        enroll_data = res.json()
        assert enroll_data["success"] is True
        node_id = enroll_data["node_id"]
        node_token = enroll_data["node_token"]
        assert node_id is not None
        assert node_token is not None

        # 4. Node Heartbeat
        headers = {"Authorization": f"Bearer {node_token}", "X-Node-ID": node_id}
        hb_req = {
            "node_id": node_id,
            "node_token": node_token,
            "status": "idle",
            "telemetry": NodeTelemetry().model_dump()
        }
        res = client.post("/api/nodes/heartbeat", json=hb_req, headers=headers)
        assert res.status_code == 200
        assert res.json()["acknowledged"] is True

        # 5. Submit job
        job_req = {
            "name": "Integration-Test-Job",
            "adapter": "gromacs",
            "requirements": {
                "software": "gromacs",
                "require_gpu": False
            },
            "parameters": {"nsteps": 100},
            "priority": 1
        }
        res = client.post("/api/jobs", json=job_req)
        assert res.status_code == 200
        job_data = res.json()
        job_id = job_data["job_id"]
        assert job_data["status"] == "pending"

        # Trigger scheduler cycle
        from chemcompute.controller.app import scheduler as app_scheduler
        app_scheduler.schedule_cycle()

        # Verify agent can poll and retrieve assigned job
        res = client.get("/api/agent/jobs/poll", headers=headers)
        assert res.status_code == 200
        poll_data = res.json()
        assert poll_data.get("job_id") == job_id

        # 6. Report progress
        prog_req = {
            "job_id": job_id,
            "node_id": node_id,
            "status": "running",
            "progress_pct": 50.0,
            "log_chunk": "Step 50 of 100: Simulation progressing normally."
        }
        res = client.post(f"/api/jobs/{job_id}/progress", json=prog_req, headers=headers)
        assert res.status_code == 200

        # 7. Check job details
        res = client.get(f"/api/jobs/{job_id}")
        assert res.status_code == 200
        job_info = res.json()
        assert job_info["progress_pct"] == 50.0
        assert "Step 50 of 100" in job_info["log_tail"]
