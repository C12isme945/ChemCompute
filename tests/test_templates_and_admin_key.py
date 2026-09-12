"""
Tests for Admin Key endpoint and calculation templates API.
"""

import io
import zipfile
from fastapi.testclient import TestClient
from chemcompute.controller.app import app


def test_admin_key_and_templates():
    with TestClient(app) as client:
        # 1. Active Admin Key endpoint
        res = client.get("/api/invite-codes/active")
        assert res.status_code == 200
        data = res.json()
        assert "admin_key" in data
        assert data["admin_key"].startswith("CC-")

        # 2. Templates list
        res = client.get("/api/templates")
        assert res.status_code == 200
        templates = res.json()
        assert len(templates) >= 2
        template_ids = [t["id"] for t in templates]
        assert "gromacs-water-smoke" in template_ids
        assert "orca-water-sp" in template_ids

        # 3. Template detail
        res = client.get("/api/templates/gromacs-water-smoke")
        assert res.status_code == 200
        detail = res.json()
        assert detail["id"] == "gromacs-water-smoke"
        assert "file_previews" in detail
        assert "min.mdp" in detail["file_previews"]

        # 4. Template download
        res = client.get("/api/templates/gromacs-water-smoke/download")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"
        
        # Verify zip contents
        z = zipfile.ZipFile(io.BytesIO(res.content))
        names = z.namelist()
        assert "min.mdp" in names
        assert "conf.gro" in names
        assert "topol.top" in names

        # 5. Job submission using template fallback
        sub_req = {
            "name": "Template-Smoke-Test",
            "adapter": "gromacs",
            "requirements": {
                "software": "gromacs",
                "require_gpu": False
            },
            "parameters": {
                "template_id": "gromacs-water-smoke"
            },
            "priority": 1
        }
        res = client.post("/api/jobs", json=sub_req)
        assert res.status_code == 200
        job_data = res.json()
        assert job_data["name"] == "Template-Smoke-Test"
        assert job_data["bundle_path"] is not None

        # Verify bundle was created on disk and is a valid zip
        bundle_res = client.get(f"/api/jobs/{job_data['job_id']}/bundle")
        assert bundle_res.status_code == 200
        job_z = zipfile.ZipFile(io.BytesIO(bundle_res.content))
        assert "min.mdp" in job_z.namelist()

        # Clean up test job so it does not interfere with scheduler tests
        app.state.db.delete_job(job_data["job_id"])
