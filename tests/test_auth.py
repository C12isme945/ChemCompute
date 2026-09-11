from __future__ import annotations

from chemcompute.common.security import (
    constant_time_compare,
    generate_admin_secret,
    hash_secret,
    verify_secret,
)


def test_constant_time_compare_and_hash():
    s1 = generate_admin_secret()
    s2 = str(s1)
    s3 = "different-secret"

    assert constant_time_compare(s1, s2) is True
    assert constant_time_compare(s1, s3) is False
    assert constant_time_compare("", "") is False
    assert constant_time_compare(123, s2) is False

    h = hash_secret(s1)
    assert verify_secret(s1, h) is True
    assert verify_secret("wrong", h) is False
    assert verify_secret("", h) is False


def test_admin_login(client, admin_secret):
    # 正确密码登录
    resp = client.post("/api/v1/admin/login", json={"token": admin_secret})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # 错误密码登录
    resp_bad = client.post("/api/v1/admin/login", json={"token": "wrong-secret"})
    assert resp_bad.status_code == 401

    # 空密码登录
    resp_empty = client.post("/api/v1/admin/login", json={"token": ""})
    assert resp_empty.status_code == 401


def test_admin_multi_method_auth(client, admin_secret):
    # 1. 未提供凭证
    r_none = client.get("/api/v1/invites")
    assert r_none.status_code == 401

    # 2. 错误 Bearer Token
    r_bad = client.get("/api/v1/invites", headers={"Authorization": "Bearer bad-token"})
    assert r_bad.status_code == 401

    # 3. 正确 Authorization: Bearer
    r_bearer = client.get(
        "/api/v1/invites",
        headers={"Authorization": f"Bearer {admin_secret}"},
    )
    assert r_bearer.status_code == 200
    assert isinstance(r_bearer.json(), list)

    # 4. 正确 X-Admin-Token 标头
    r_header = client.get(
        "/api/v1/invites",
        headers={"X-Admin-Token": admin_secret},
    )
    assert r_header.status_code == 200

    # 5. 正确 Cookie
    client.cookies.set("chemcompute_admin_token", admin_secret)
    r_cookie = client.get("/api/v1/invites")
    assert r_cookie.status_code == 401
