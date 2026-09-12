"""
Tests for Controller Release Management, Rollout Channels (Canary/Beta/Stable), and Update Check APIs.
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from chemcompute.controller.app import app
from chemcompute.controller.database import Database
from chemcompute.common.models import ReleaseChannel


@pytest.fixture
def client(tmp_path):
    test_db = Database(tmp_path / "test.db")
    app.state.db = test_db
    app.state.storage_root = tmp_path / "storage"
    app.state.storage_root.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as tc:
        yield tc, test_db


def test_channel_rollout_lifecycle(client):
    tc, db = client

    # 1. Create an invite code and enroll two nodes:
    # Node A -> Canary, Node B -> Stable
    inv_res = tc.post("/api/invite-codes", json={"max_uses": 5, "expires_in_days": 1})
    assert inv_res.status_code == 200
    code = inv_res.json()["code"]

    resp_a = tc.post("/api/nodes/enroll", json={
        "invite_code": code,
        "node_name": "Node-Canary",
        "agent_version": "0.1.0",
        "channel": "canary"
    })
    node_a_id = resp_a.json()["node_id"]

    resp_b = tc.post("/api/nodes/enroll", json={
        "invite_code": code,
        "node_name": "Node-Stable",
        "agent_version": "0.1.0",
        "channel": "stable"
    })
    node_b_id = resp_b.json()["node_id"]

    # 2. Publish release v0.2.0 to Canary channel
    release_payload = {
        "version": "0.2.0",
        "component": "agent",
        "channel": "canary",
        "package_url": "http://127.0.0.1:8000/releases/agent_0.2.0.zip",
        "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "changelog": "Initial canary testing build for 0.2.0"
    }
    rel_res = tc.post("/api/v1/releases", json=release_payload)
    assert rel_res.status_code == 200

    # 3. Check update for Node A (Canary): MUST see update available!
    check_a = tc.get(f"/api/v1/update/check?node_id={node_a_id}&agent_version=0.1.0")
    assert check_a.status_code == 200
    res_a = check_a.json()
    assert res_a["update_available"] is True
    assert res_a["version"] == "0.2.0"

    # 4. Check update for Node B (Stable): MUST NOT see update yet!
    check_b = tc.get(f"/api/v1/update/check?node_id={node_b_id}&agent_version=0.1.0")
    assert check_b.status_code == 200
    res_b = check_b.json()
    assert res_b["update_available"] is False

    # 5. Promote v0.2.0 to Stable channel
    promote_res = tc.post("/api/v1/releases/0.2.0/promote", json={"to_channel": "stable"})
    assert promote_res.status_code == 200
    assert promote_res.json()["status"] == "promoted"

    # 6. Check update for Node B (Stable) again: MUST now see update available!
    check_b2 = tc.get(f"/api/v1/update/check?node_id={node_b_id}&agent_version=0.1.0")
    assert check_b2.status_code == 200
    res_b2 = check_b2.json()
    assert res_b2["update_available"] is True
    assert res_b2["version"] == "0.2.0"

    # 7. Test node channel update
    ch_res = tc.post(f"/api/v1/nodes/{node_b_id}/channel", json={"channel": "beta"})
    assert ch_res.status_code == 200
    assert ch_res.json()["channel"] == "beta"

    # 8. List releases endpoint
    list_res = tc.get("/api/v1/releases")
    assert list_res.status_code == 200
    data = list_res.json()
    assert len(data["releases"]) >= 2
    assert data["total_nodes"] == 2
