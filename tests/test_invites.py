from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chemcompute.common.models import HardwareInfo, SoftwareInfo
from chemcompute.common.security import generate_invite_code, hash_secret


def _dummy_node_payload(invite_code: str, node_name: str = "TestNode-1"):
    return {
        "invite_code": invite_code,
        "node_name": node_name,
        "hostname": "worker-host-1",
        "os": "Windows 11",
        "hardware": HardwareInfo().model_dump(),
        "software": SoftwareInfo(hostname="worker-host-1").model_dump(),
    }


def test_invite_creation_and_registration(client, admin_secret):
    headers = {"Authorization": f"Bearer {admin_secret}"}

    # 1. 管理员创建单次邀请码
    res = client.post(
        "/api/v1/invites",
        json={"expires_in_hours": 12, "note": "Node-Alpha-Join"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    code = data["invite_code"]
    assert code.startswith("cc-inv-")

    # 2. 节点使用该邀请码首次注册
    reg_resp = client.post("/api/v1/nodes/register", json=_dummy_node_payload(code, "Node-Alpha"))
    assert reg_resp.status_code == 200
    reg_data = reg_resp.json()
    assert "node_id" in reg_data
    assert "node_token" in reg_data
    assert reg_data["node_token"].startswith("cc-node-")


def test_invite_replay_protection(client, admin_secret):
    """验证邀请码重放攻击防御：单次使用后再次使用必须被拒绝 (HTTP 403)"""
    headers = {"Authorization": f"Bearer {admin_secret}"}

    # 1. 创建邀请码
    res = client.post(
        "/api/v1/invites",
        json={"expires_in_hours": 24, "note": "Replay-Test"},
        headers=headers,
    )
    assert res.status_code == 200
    code = res.json()["invite_code"]

    # 2. 第一次正常消费注册
    reg1 = client.post("/api/v1/nodes/register", json=_dummy_node_payload(code, "Node-Replay-1"))
    assert reg1.status_code == 200

    # 3. 第二次尝试使用同一邀请码 (重放) -> 必须被拒绝
    reg2 = client.post("/api/v1/nodes/register", json=_dummy_node_payload(code, "Node-Replay-2"))
    assert reg2.status_code == 403
    assert "无效、已过期或已被消耗" in reg2.json()["detail"]


def test_invite_expiration(client, admin_secret):
    """验证邀请码过期判定：已过期的邀请码不可被注册 (HTTP 403)"""
    db = client.app.state.db

    # 插入一个 1 小时前已过期的邀请码
    expired_code = generate_invite_code("cc-expired-")
    code_hash = hash_secret(expired_code)
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    db.create_invite(
        code_hash=code_hash,
        code_prefix=expired_code[:12] + "...",
        expires_at=past_iso,
        note="Expired Test",
    )

    # 尝试使用已过期的邀请码注册
    reg = client.post("/api/v1/nodes/register", json=_dummy_node_payload(expired_code, "Node-Expired"))
    assert reg.status_code == 403
    assert "无效、已过期或已被消耗" in reg.json()["detail"]


def test_invalid_invite_code(client):
    """测试完全伪造的邀请码"""
    reg = client.post("/api/v1/nodes/register", json=_dummy_node_payload("cc-inv-fake-fake-fake"))
    assert reg.status_code == 403


def test_invite_revocation(client, admin_secret):
    """测试撤销邀请码"""
    headers = {"Authorization": f"Bearer {admin_secret}"}
    res = client.post(
        "/api/v1/invites",
        json={"expires_in_hours": 24, "note": "To-Revoke"},
        headers=headers,
    )
    assert res.status_code == 200
    invites = client.get("/api/v1/invites", headers=headers).json()
    inv_id = invites[0]["id"]

    del_res = client.delete(f"/api/v1/invites/{inv_id}", headers=headers)
    assert del_res.status_code == 200

    # 撤销后不可再次撤销
    del_res2 = client.delete(f"/api/v1/invites/{inv_id}", headers=headers)
    assert del_res2.status_code == 404
