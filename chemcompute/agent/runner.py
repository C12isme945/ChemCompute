"""
Execution runner for ChemCompute Agent sandbox.
"""

import os
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional
import httpx

from chemcompute.adapters.gromacs import GromacsAdapter
from chemcompute.agent.plugin_manager import PluginManager
from chemcompute.common.models import JobStatus, JobProgressUpdate


class JobRunner:
    """Executes a chemistry job in an isolated sandbox workspace."""

    def __init__(
        self,
        workspace_root: Path,
        controller_url: str,
        node_id: str,
        node_token: str,
        plugin_manager: Optional[PluginManager] = None
    ):
        self.workspace_root = workspace_root
        self.controller_url = controller_url.rstrip("/")
        self.node_id = node_id
        self.node_token = node_token
        self.plugin_manager = plugin_manager or PluginManager()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.node_token}", "X-Node-ID": self.node_id}

    def update_progress(self, job_id: str, status: JobStatus, progress_pct: float, log_chunk: str, error: Optional[str] = None):
        """Send live progress updates to Controller."""
        try:
            update = JobProgressUpdate(
                job_id=job_id,
                node_id=self.node_id,
                status=status,
                progress_pct=progress_pct,
                log_chunk=log_chunk,
                error_message=error
            )
            with httpx.Client(timeout=5) as client:
                client.post(
                    f"{self.controller_url}/api/jobs/{job_id}/progress",
                    json=update.model_dump(),
                    headers=self._auth_headers()
                )
        except Exception:
            pass

    def run_job(self, job_data: Dict[str, Any]) -> bool:
        """Download bundle, execute with matching adapter, report progress, and upload results."""
        job_id = job_data["job_id"]
        adapter_name = job_data.get("adapter", "gromacs").lower()
        parameters = job_data.get("parameters", {})
        job_dir = self.workspace_root / f"job_{job_id}"

        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, exist_ok=True)

        self.update_progress(job_id, JobStatus.RUNNING, 2.0, f"[RUNNER] Initializing sandbox for job {job_id}")

        adapter = self.plugin_manager.get_adapter(adapter_name)
        if not adapter:
            err = f"Adapter '{adapter_name}' is not supported on this node."
            self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
            return False

        # 1. Download input bundle from controller if available
        bundle_url = f"{self.controller_url}/api/jobs/{job_id}/bundle"
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.get(bundle_url, headers=self._auth_headers())
                if resp.status_code == 200 and resp.content:
                    bundle_path = job_dir / "bundle.zip"
                    bundle_path.write_bytes(resp.content)
                    with zipfile.ZipFile(bundle_path, "r") as zf:
                        zf.extractall(job_dir)
                    self.update_progress(job_id, JobStatus.RUNNING, 5.0, "[RUNNER] Job bundle downloaded and extracted.")
                elif resp.status_code != 404:
                    err = f"Failed to download job bundle: HTTP {resp.status_code}"
                    self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
                    return False
        except Exception as ex:
            err = f"Error fetching input bundle: {str(ex)}"
            self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
            return False

        # 2. Prepare inputs
        self.update_progress(job_id, JobStatus.RUNNING, 8.0, f"[RUNNER] Preparing {adapter_name} calculation...")
        prep_ok = adapter.prepare(job_dir, parameters)
        if not prep_ok:
            err = f"Preparation step failed for {adapter_name}."
            self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
            return False

        # 3. Execute with live callback
        def on_progress(pct: float, log_line: str):
            self.update_progress(job_id, JobStatus.RUNNING, pct, log_line)

        exec_ok = adapter.run(job_dir, parameters, progress_callback=on_progress)
        if not exec_ok:
            err = f"Computation execution failed for {adapter_name}."
            self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
            return False

        # 4. Collect results
        self.update_progress(job_id, JobStatus.RUNNING, 98.0, "[RUNNER] Collecting and compressing results...")
        result_files = adapter.collect(job_dir)
        zip_artifact = None
        for rf in result_files:
            if rf.suffix.lower() == ".zip":
                zip_artifact = rf
                break

        # 5. Upload results to controller
        if zip_artifact and zip_artifact.exists():
            upload_url = f"{self.controller_url}/api/jobs/{job_id}/results"
            try:
                with httpx.Client(timeout=120) as client:
                    with open(zip_artifact, "rb") as f:
                        files = {"file": (zip_artifact.name, f, "application/zip")}
                        resp = client.post(upload_url, files=files, headers=self._auth_headers())
                        if resp.status_code != 200:
                            err = f"Failed to upload result archive: HTTP {resp.status_code}"
                            self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
                            return False
            except Exception as ex:
                err = f"Result upload error: {str(ex)}"
                self.update_progress(job_id, JobStatus.FAILED, 0.0, err, error=err)
                return False

        self.update_progress(job_id, JobStatus.COMPLETED, 100.0, "[SUCCESS] Job finished and results successfully uploaded.")
        return True
