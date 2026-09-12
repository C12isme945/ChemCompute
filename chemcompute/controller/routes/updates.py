"""
Release management, channel routing, and hot update REST endpoints.
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from chemcompute.common.models import (
    ComponentType,
    DynamicNodeConfig,
    ReleaseChannel,
    ReleaseManifest,
    ReleasePackageInfo,
    UpdateCheckResponse,
    UpdateStatus,
)
from chemcompute.controller.websocket import ws_manager

router = APIRouter(prefix="/api/v1", tags=["updates"])


def is_newer_version(new_ver: str, curr_ver: str) -> bool:
    try:
        def parse(v: str):
            clean = v.split("-")[0].split("+")[0]
            return tuple(int(x) for x in clean.split("."))
        return parse(new_ver) > parse(curr_ver)
    except Exception:
        return new_ver != curr_ver


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


@router.get("/update/check", response_model=UpdateCheckResponse)
def check_update(
    node_id: str,
    agent_version: str = "0.1.0",
    channel: Optional[str] = None,
    platform: str = "windows-x64",
    request: Request = None
):
    """
    Check if an update is available for this node based on its channel and current version.
    """
    db = request.app.state.db

    # Look up registered channel if not provided
    node = db.get_node(node_id)
    if node and not channel:
        effective_channel = node.channel
    elif channel:
        try:
            effective_channel = ReleaseChannel(channel.lower())
        except ValueError:
            effective_channel = ReleaseChannel.STABLE
    else:
        effective_channel = ReleaseChannel.STABLE

    latest = db.get_latest_release(channel=effective_channel, component=ComponentType.AGENT)
    if not latest:
        return UpdateCheckResponse(update_available=False)

    if is_newer_version(latest.version, agent_version):
        return UpdateCheckResponse(
            update_available=True,
            version=latest.version,
            component=latest.component,
            adapter_name=latest.adapter_name,
            mandatory=False,
            url=latest.package.url,
            sha256=latest.package.sha256,
            size_bytes=latest.package.size_bytes,
            changelog=latest.changelog
        )

    return UpdateCheckResponse(update_available=False)


@router.get("/releases")
def list_releases(
    channel: Optional[str] = None,
    component: Optional[str] = None,
    request: Request = None
):
    """List all releases and cluster channel summary."""
    db = request.app.state.db

    c_filter = ReleaseChannel(channel.lower()) if channel else None
    comp_filter = ComponentType(component.lower()) if component else None
    releases = db.list_releases(component=comp_filter, channel=c_filter)

    # Node distribution across channels
    nodes = db.list_nodes()
    channel_counts = {"canary": 0, "beta": 0, "stable": 0}
    for n in nodes:
        ch = n.channel.value if isinstance(n.channel, ReleaseChannel) else str(n.channel)
        channel_counts[ch] = channel_counts.get(ch, 0) + 1

    return {
        "releases": [r.model_dump() for r in releases],
        "channel_counts": channel_counts,
        "total_nodes": len(nodes)
    }


class ReleaseUploadMetadata(BaseModel):
    version: str
    component: ComponentType = ComponentType.AGENT
    adapter_name: Optional[str] = None
    channel: ReleaseChannel = ReleaseChannel.CANARY
    changelog: str = ""
    minimum_agent_version: str = "0.1.0"
    package_url: Optional[str] = None
    sha256: Optional[str] = None


@router.post("/releases")
async def create_release(
    request: Request,
    package: Optional[UploadFile] = File(None),
    metadata: Optional[str] = Form(None)
):
    """
    Create a new release. Supports either uploading a package file (.zip) or providing an external URL.
    """
    db = request.app.state.db
    storage_root: Path = request.app.state.storage_root

    if metadata:
        meta_dict = json.loads(metadata)
        meta = ReleaseUploadMetadata.model_validate(meta_dict)
    else:
        body = await request.json()
        meta = ReleaseUploadMetadata.model_validate(body)

    releases_dir = storage_root / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)

    package_url = meta.package_url or ""
    sha256 = meta.sha256 or ""
    size_bytes = 0
    filename = None

    if package:
        filename = package.filename or f"release_{meta.version}.zip"
        dest_path = releases_dir / f"{meta.component.value}_{meta.version}_{filename}"
        content = await package.read()
        dest_path.write_bytes(content)
        size_bytes = len(content)
        sha256 = compute_file_sha256(dest_path)
        package_url = f"/api/v1/releases/{meta.version}/download?component={meta.component.value}"

    manifest = ReleaseManifest(
        version=meta.version,
        component=meta.component,
        adapter_name=meta.adapter_name,
        channel=meta.channel,
        package=ReleasePackageInfo(
            url=package_url,
            sha256=sha256,
            size_bytes=size_bytes,
            filename=filename
        ),
        minimum_agent_version=meta.minimum_agent_version,
        changelog=meta.changelog
    )

    db.create_or_update_release(manifest)

    # Broadcast notification to consoles
    await ws_manager.broadcast_to_consoles({
        "type": "release.created",
        "version": manifest.version,
        "channel": manifest.channel.value
    })

    return manifest


class PromoteRequest(BaseModel):
    to_channel: ReleaseChannel
    component: ComponentType = ComponentType.AGENT
    adapter_name: Optional[str] = None


@router.post("/releases/{version}/promote")
async def promote_release(version: str, req: PromoteRequest, request: Request):
    """
    Promote a release to another channel (Canary -> Beta -> Stable).
    """
    db = request.app.state.db
    success = db.promote_release(
        version=version,
        to_channel=req.to_channel,
        component=req.component,
        adapter_name=req.adapter_name
    )
    if not success:
        raise HTTPException(status_code=404, detail="Release version not found to promote.")

    # Notify connected nodes on that channel
    promoted = db.get_release(version, component=req.component, channel=req.to_channel, adapter_name=req.adapter_name)
    if promoted:
        await ws_manager.broadcast_to_nodes({
            "type": "agent.update_available" if req.component == ComponentType.AGENT else "adapter.update_available",
            "version": version,
            "component": req.component.value,
            "channel": req.to_channel.value,
            "url": promoted.package.url,
            "sha256": promoted.package.sha256
        })

    return {"status": "promoted", "version": version, "to_channel": req.to_channel.value}


@router.get("/releases/{version}/download")
def download_release_package(
    version: str,
    component: str = "agent",
    channel: Optional[str] = None,
    request: Request = None
):
    """Download stored release package artifact."""
    db = request.app.state.db
    storage_root: Path = request.app.state.storage_root
    releases_dir = storage_root / "releases"

    comp_type = ComponentType(component.lower())
    chan_type = ReleaseChannel(channel.lower()) if channel else ReleaseChannel.STABLE

    # Try finding release
    rel = db.get_release(version, component=comp_type, channel=chan_type)
    if not rel:
        # Fallback to any channel
        all_rels = [r for r in db.list_releases(component=comp_type) if r.version == version]
        if all_rels:
            rel = all_rels[0]

    if not rel:
        raise HTTPException(status_code=404, detail="Release not found.")

    # Look for file on disk
    filename = rel.package.filename or f"release_{version}.zip"
    dest_path = releases_dir / f"{comp_type.value}_{version}_{filename}"
    if not dest_path.exists():
        # Check if direct filename match exists
        for p in releases_dir.glob(f"*{version}*"):
            dest_path = p
            break

    if not dest_path.exists():
        raise HTTPException(status_code=404, detail="Release package file not found on controller disk.")

    return FileResponse(dest_path, media_type="application/zip", filename=dest_path.name)


class ChannelUpdateRequest(BaseModel):
    channel: ReleaseChannel


@router.post("/nodes/{node_id}/channel")
def set_node_channel(node_id: str, req: ChannelUpdateRequest, request: Request):
    """Set the rollout channel for a node (canary, beta, stable)."""
    db = request.app.state.db
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")
    db.update_node_channel(node_id, req.channel)
    return {"status": "ok", "node_id": node_id, "channel": req.channel.value}


@router.post("/nodes/{node_id}/update")
async def push_update_to_node(node_id: str, request: Request):
    """Manually push the latest applicable release to a single node."""
    db = request.app.state.db
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")

    latest = db.get_latest_release(channel=node.channel, component=ComponentType.AGENT)
    if not latest:
        raise HTTPException(status_code=400, detail=f"No release available in channel {node.channel.value}")

    # Send WebSocket update command
    msg = {
        "type": "agent.update_available",
        "version": latest.version,
        "component": latest.component.value,
        "channel": latest.channel.value,
        "url": latest.package.url,
        "sha256": latest.package.sha256,
        "changelog": latest.changelog,
        "mandatory": False
    }
    sent = await ws_manager.send_to_node(node_id, msg)
    db.update_node_update_status(node_id, UpdateStatus.UPDATE_AVAILABLE)

    return {"status": "dispatched", "via_websocket": sent, "target_version": latest.version}


class ConfigPushRequest(BaseModel):
    config: Dict[str, Any]


@router.post("/nodes/{node_id}/config")
async def push_config_to_node(node_id: str, req: ConfigPushRequest, request: Request):
    """Push configuration patch to a node."""
    db = request.app.state.db
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")

    msg = {
        "type": "config.push",
        "config": req.config
    }
    sent = await ws_manager.send_to_node(node_id, msg)
    return {"status": "pushed", "via_websocket": sent}


class BatchPushRequest(BaseModel):
    channel: Optional[ReleaseChannel] = None
    component: ComponentType = ComponentType.AGENT


@router.post("/update/push-all")
async def push_update_all(req: BatchPushRequest, request: Request):
    """Broadcast update push to all nodes in a channel or entire cluster."""
    db = request.app.state.db
    nodes = db.list_nodes()

    count = 0
    for n in nodes:
        target_chan = req.channel or n.channel
        latest = db.get_latest_release(channel=target_chan, component=req.component)
        if latest and is_newer_version(latest.version, n.agent_version):
            msg = {
                "type": "agent.update_available" if req.component == ComponentType.AGENT else "adapter.update_available",
                "version": latest.version,
                "component": latest.component.value,
                "channel": latest.channel.value,
                "url": latest.package.url,
                "sha256": latest.package.sha256,
                "changelog": latest.changelog
            }
            await ws_manager.send_to_node(n.node_id, msg)
            db.update_node_update_status(n.node_id, UpdateStatus.UPDATE_AVAILABLE)
            count += 1

    return {"status": "broadcast_complete", "notified_nodes": count}
