import subprocess
import sys
from pathlib import Path

from chemcompute.node.adapters.gromacs import GromacsAdapter
from chemcompute.node.process import run_capped
from tests.test_invites import _dummy_node_payload


def test_heartbeat_and_auth_boundaries(client, admin_secret):
    admin = {"Authorization": f"Bearer {admin_secret}"}
    assert client.get('/api/v1/overview').status_code == 401
    assert client.get('/api/v1/nodes').status_code == 401
    code = client.post('/api/v1/invites', json={}, headers=admin).json()['invite_code']
    node = client.post('/api/v1/nodes/register', json=_dummy_node_payload(code)).json()
    url = f"/api/v1/nodes/{node['node_id']}/heartbeat"
    payload = {'node_id': node['node_id'], 'hardware': {}, 'software': {}}
    assert client.post(url, json=payload).status_code == 401
    auth = {'Authorization': f"Bearer {node['node_token']}"}
    assert client.post(url, json=payload, headers=auth).status_code == 200
    payload['node_id'] = 'another-node'
    assert client.post(url, json=payload, headers=auth).status_code == 400
    assert client.get('/api/v1/nodes', headers=admin).json()[0]['status'] == 'online'
    # Database contains hashes, never raw node credentials.
    db_path = Path(client.app.state.db.db_path)
    assert node['node_token'].encode() not in db_path.read_bytes()


def test_adapter_rejects_escaping_paths(tmp_path):
    adapter = GromacsAdapter(workspace_dir=tmp_path / 'jobs')
    for arg in ['../secret', 'C:\\secret', '/etc/passwd', '..\\secret', 'x;evil']:
        assert adapter.run_bounded('mdrun', ['-s', arg]).exit_code == -1
    assert adapter.run_bounded('shell', []).exit_code == -1
    assert adapter.run_bounded('mdrun', [], custom_cwd=tmp_path).exit_code == -1


def test_subprocess_output_is_bounded():
    result = run_capped([sys.executable, '-c', 'print("x" * 1000000)'])
    assert result.returncode == 0
    assert len(result.stdout) == 500_000


def test_subprocess_timeout():
    import pytest
    with pytest.raises(subprocess.TimeoutExpired):
        run_capped([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.1)
