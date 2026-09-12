"""Protect the installed v0.3 API and database contract during UI/update changes."""

import pytest
from fastapi.testclient import TestClient

from chemcompute.common.config import ControllerConfig, NodeConfig
from chemcompute.controller.app import create_app


@pytest.mark.parametrize('path', [
    '/api/v1/nodes', '/api/v1/invites', '/api/v1/jobs', '/api/v2/tasks',
])
def test_existing_management_routes_require_auth(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(('method', 'path'), [
    ('GET', '/api/invite-codes/active'),
    ('POST', '/api/jobs'),
    ('POST', '/api/v1/releases'),
])
def test_incompatible_unauthenticated_routes_are_not_exposed(client, method, path):
    assert client.request(method, path, json={}).status_code == 404


def test_gaussian_setting_survives_config_roundtrip():
    config = NodeConfig(gaussian_custom_path='C:/G16W/g16.exe')
    assert NodeConfig.model_validate(config.model_dump()).gaussian_custom_path == config.gaussian_custom_path


def test_controller_reopens_existing_runtime_without_replacing_invites(tmp_path):
    config = ControllerConfig(db_path=str(tmp_path / 'existing.db'), admin_secret='test-upgrade-secret')
    headers = {'Authorization': 'Bearer test-upgrade-secret'}
    with TestClient(create_app(config)) as first:
        created = first.post('/api/v1/invites', headers=headers, json={'note': 'upgrade-contract'})
        assert created.status_code == 200
        before = first.get('/api/v1/invites', headers=headers).json()
    with TestClient(create_app(config)) as second:
        assert second.get('/api/v1/invites', headers=headers).json() == before
        assert second.get('/api/v1/nodes', headers=headers).status_code == 200
        assert second.get('/api/v1/jobs', headers=headers).status_code == 200
        assert second.get('/api/v2/tasks', headers=headers).status_code == 200
