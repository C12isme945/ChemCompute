"""
Node management and heartbeat REST API endpoints.
"""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Header, HTTPException, Request

from chemcompute.common.models import (
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    NodeInfo,
)
from chemcompute.common.security import generate_token, hash_token

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def is_newer_version(new_ver: str, curr_ver: str) -> bool:
    try:
        def parse(v: str):
            clean = v.split("-")[0].split("+")[0]
            return tuple(int(x) for x in clean.split("."))
        return parse(new_ver) > parse(curr_ver)
    except Exception:
        return new_ver != curr_ver


@router.post("/enroll", response_model=EnrollmentResponse)
def enroll_node(req: EnrollmentRequest, request: Request):
    db = request.app.state.db
    # 1. Validate and consume invite code
    if not db.verify_and_consume_invite_code(req.invite_code.strip().upper()):
        return EnrollmentResponse(
            success=False,
            message="Invalid, expired, or depleted invite code."
        )

    # 2. Generate node identity & credentials
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    node_token = generate_token()
    token_hash = hash_token(node_token)

    # 3. Register in DB
    db.register_node(
        node_id=node_id,
        node_name=req.node_name,
        token_hash=token_hash,
        ip_address=req.ip_address,
        role=req.role,
        software=req.software,
        telemetry=req.telemetry,
        agent_version=req.agent_version,
        channel=req.channel
    )

    return EnrollmentResponse(
        success=True,
        node_id=node_id,
        node_token=node_token,
        message="Successfully registered node to ChemCompute cluster.",
        assigned_channel=req.channel
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
def node_heartbeat(
    req: HeartbeatRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_node_id: Optional[str] = Header(None)
):
    db = request.app.state.db
    token = req.node_token
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]

    node_id = req.node_id or x_node_id
    token_hash = hash_token(token)
    if not db.verify_node_token(node_id, token_hash):
        raise HTTPException(status_code=401, detail="Invalid node token or identity.")

    db.update_node_heartbeat(
        node_id=node_id,
        status=req.status,
        telemetry=req.telemetry,
        active_job_id=req.active_job_id,
        agent_version=req.agent_version,
        channel=req.channel,
        update_status=req.update_status
    )

    # Check if update is available for this node
    node_info = db.get_node(node_id)
    update_resp = None
    if node_info:
        latest = db.get_latest_release(channel=node_info.channel, component="agent")
        if latest and is_newer_version(latest.version, node_info.agent_version):
            from chemcompute.common.models import UpdateCheckResponse
            update_resp = UpdateCheckResponse(
                update_available=True,
                version=latest.version,
                component=latest.component,
                url=latest.package.url,
                sha256=latest.package.sha256,
                size_bytes=latest.package.size_bytes,
                changelog=latest.changelog
            )

    return HeartbeatResponse(acknowledged=True, update_available=update_resp)


@router.get("", response_model=List[NodeInfo])
def list_nodes(request: Request):
    db = request.app.state.db
    return db.list_nodes()


@router.get("/{node_id}", response_model=NodeInfo)
def get_node(node_id: str, request: Request):
    db = request.app.state.db
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")
    return node
