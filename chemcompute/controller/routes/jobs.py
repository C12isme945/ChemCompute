"""
Job lifecycle, submission, agent polling, and results REST API endpoints.
"""

import json
import uuid
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

from chemcompute.common.models import (
    JobDetail,
    JobSubmission,
    JobProgressUpdate,
    JobStatus,
)
from chemcompute.common.security import hash_token

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/jobs", response_model=JobDetail)
async def submit_job(
    request: Request,
    bundle: Optional[UploadFile] = File(None),
    metadata: Optional[str] = Form(None)
):
    """
    Submit a calculation job.
    Supports either multipart form (bundle + metadata JSON) or JSON body.
    """
    db = request.app.state.db
    storage_root: Path = request.app.state.storage_root

    # Parse metadata
    if metadata:
        meta_dict = json.loads(metadata)
        submission = JobSubmission.model_validate(meta_dict)
    else:
        # Check if raw JSON body
        body = await request.json()
        submission = JobSubmission.model_validate(body)

    job_id = f"job-{uuid.uuid4().hex[:8]}"

    # Save bundle if uploaded, or fallback to standard calculation template
    bundle_path_str = None
    bundles_dir = storage_root / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)

    if bundle:
        dest_path = bundles_dir / f"{job_id}.zip"
        content = await bundle.read()
        dest_path.write_bytes(content)
        bundle_path_str = str(dest_path)
    else:
        # Check template fallback
        template_id = submission.parameters.get("template_id")
        if not template_id and submission.adapter == "gromacs":
            template_id = "gromacs-water-smoke"
        elif not template_id and submission.adapter == "orca":
            template_id = "orca-water-sp"

        if template_id:
            from chemcompute.controller.routes.templates import TEMPLATES_CATALOG
            matched = next((t for t in TEMPLATES_CATALOG if t["id"] == template_id), None)
            if matched:
                dest_path = bundles_dir / f"{job_id}.zip"
                import zipfile
                with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as z:
                    for fname, fcontent in matched["file_previews"].items():
                        z.writestr(fname, fcontent)
                bundle_path_str = str(dest_path)

    job = db.create_job(
        job_id=job_id,
        name=submission.name,
        adapter=submission.adapter,
        requirements=submission.requirements,
        parameters=submission.parameters,
        bundle_path=bundle_path_str,
        priority=submission.priority
    )
    return job


@router.get("/jobs", response_model=List[JobDetail])
def list_jobs(request: Request):
    db = request.app.state.db
    return db.list_jobs()


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, request: Request):
    db = request.app.state.db
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.get("/jobs/{job_id}/bundle")
def download_bundle(job_id: str, request: Request):
    db = request.app.state.db
    job = db.get_job(job_id)
    if not job or not job.bundle_path:
        raise HTTPException(status_code=404, detail="No bundle found for this job.")

    path = Path(job.bundle_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Bundle file missing on disk.")

    return FileResponse(path, media_type="application/zip", filename=f"{job_id}_input.zip")


@router.post("/jobs/{job_id}/progress")
def update_progress(
    job_id: str,
    update: JobProgressUpdate,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_node_id: Optional[str] = Header(None)
):
    db = request.app.state.db
    db.update_job_progress(
        job_id=job_id,
        status=update.status,
        progress_pct=update.progress_pct,
        log_chunk=update.log_chunk,
        error_message=update.error_message
    )
    return {"status": "ok"}


@router.post("/jobs/{job_id}/results")
async def upload_results(
    job_id: str,
    request: Request,
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
    x_node_id: Optional[str] = Header(None)
):
    db = request.app.state.db
    storage_root: Path = request.app.state.storage_root

    results_dir = storage_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    dest_path = results_dir / f"{job_id}_results.zip"

    content = await file.read()
    dest_path.write_bytes(content)

    db.set_job_result_path(job_id, str(dest_path))
    return {"status": "uploaded", "path": str(dest_path)}


@router.get("/jobs/{job_id}/results")
def download_results(job_id: str, request: Request):
    db = request.app.state.db
    job = db.get_job(job_id)
    if not job or not job.result_archive:
        raise HTTPException(status_code=404, detail="No results archive found for this job.")

    path = Path(job.result_archive)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result archive missing on disk.")

    return FileResponse(path, media_type="application/zip", filename=f"{job_id}_results.zip")


@router.get("/agent/jobs/poll")
def agent_poll_job(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_node_id: Optional[str] = Header(None)
):
    """Agent polls for next assigned job."""
    db = request.app.state.db
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]

    if not x_node_id or not token:
        raise HTTPException(status_code=401, detail="Missing node credentials.")

    if not db.verify_node_token(x_node_id, hash_token(token)):
        raise HTTPException(status_code=401, detail="Invalid node credentials.")

    assigned_job = db.get_assigned_job_for_node(x_node_id)
    if not assigned_job:
        return {}

    return assigned_job.model_dump()
