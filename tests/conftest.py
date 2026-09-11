from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chemcompute.common.config import ControllerConfig
from chemcompute.controller.app import create_app


@pytest.fixture
def admin_secret() -> str:
    return "test-adm-secret-super-secure-token-998877"


@pytest.fixture
def test_app(tmp_path, admin_secret):
    db_file = tmp_path / "test_chemcompute.db"
    secret_file = tmp_path / "test_admin.secret"
    config = ControllerConfig(
        host="127.0.0.1",
        port=8000,
        db_path=str(db_file),
        admin_secret=admin_secret,
        secret_file=str(secret_file),
        node_offline_threshold_seconds=10,
        heartbeat_interval_seconds=2,
    )
    app = create_app(config)
    return app


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as c:
        yield c
