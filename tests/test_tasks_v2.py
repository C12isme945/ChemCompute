import hashlib
import io
import zipfile

import pytest


def archive(name='input.gro'):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as package:
        package.writestr(name, 'unit test input')
    return output.getvalue()


def node(client, headers):
    invite = client.post('/api/v1/invites', headers=headers, json={}).json()['invite_code']
    value = client.post('/api/v1/nodes/register', json={
        'invite_code': invite, 'node_name': 'test worker', 'hostname': 'test', 'os': 'test',
        'hardware': {'cpu_count_logical': 4, 'ram_available_mb': 8192},
        'software': {'gromacs': {'found': True}},
    }).json()
    return value['node_id'], {'Authorization': 'Bearer ' + value['node_token']}


def test_transfer_lifecycle_and_cross_node_access(client, admin_secret):
    admin = {'Authorization': 'Bearer ' + admin_secret}
    content = archive()
    assert client.post('/api/v2/packages', content=content).status_code == 401
    assert client.post('/api/v2/packages', headers=admin, content=archive('../escape')).status_code == 400
    assert client.post('/api/v2/packages', headers=admin, content=b'invalid').status_code == 400
    uploaded = client.post('/api/v2/packages', headers=admin, content=content).json()
    worker, auth = node(client, admin)
    other, other_auth = node(client, admin)
    task = client.post('/api/v2/tasks', headers=admin, json={
        'name': 'roundtrip', 'package_id': uploaded['id'], 'steps': [{'subcommand': 'check'}],
    }).json()['id']
    assert client.post(f'/api/v2/worker/{worker}/claim', headers=auth).json()['id'] == task
    assert client.post(f'/api/v2/worker/{worker}/claim', headers=auth).json() is None
    path = f'/api/v2/worker/{worker}/tasks/{task}'
    other_path = f'/api/v2/worker/{other}/tasks/{task}'
    for suffix in ['', '/input']:
        assert client.get(other_path + suffix, headers=other_auth).status_code == 403
    assert client.put(other_path + '/results', headers=other_auth, content=content).status_code == 403
    assert client.post(other_path + '/progress', headers=other_auth, json={'status': 'completed'}).status_code == 403
    response = client.get(path + '/input', headers=auth)
    assert response.content == content
    assert response.headers['X-SHA256'] == hashlib.sha256(content).hexdigest()
    assert client.post(path + '/progress', headers=auth, json={'status': 'completed'}).status_code == 409
    assert client.put(path + '/results', headers=auth, content=content).status_code == 200
    assert client.post(path + '/progress', headers=auth, json={'status': 'completed', 'progress': 100}).status_code == 200
    result = client.get(f'/api/v2/tasks/{task}/results', headers=admin)
    assert result.content == content
    assert result.headers['X-SHA256'] == hashlib.sha256(content).hexdigest()


def test_priority_resources_cancel_and_retry(client, admin_secret):
    admin = {'Authorization': 'Bearer ' + admin_secret}
    worker, auth = node(client, admin)
    def submit(priority, gpu=False):
        return client.post('/api/v2/tasks', headers=admin, json={
            'name': 'queue', 'priority': priority, 'resources': {'gpu': gpu},
            'steps': [{'subcommand': 'version'}],
        }).json()['id']
    low, high, gpu = submit(1), submit(5), submit(10, True)
    claim_url = f'/api/v2/worker/{worker}/claim'
    assert client.post(claim_url, headers=auth).json()['id'] == high
    assert client.post(f'/api/v2/tasks/{high}/retry', headers=admin).status_code == 409
    client.post(f'/api/v2/tasks/{high}/cancel', headers=admin)
    status_url = f'/api/v2/worker/{worker}/tasks/{high}'
    assert client.get(status_url, headers=auth).json()['cancelled']
    client.post(status_url + '/progress', headers=auth, json={'status': 'cancelled'})
    assert client.post(claim_url, headers=auth).json()['id'] == low
    cancelled = client.post(f'/api/v2/tasks/{gpu}/cancel', headers=admin).json()
    assert cancelled['status'] == 'cancelled'
    retry = client.post(f'/api/v2/tasks/{gpu}/retry', headers=admin).json()
    assert retry['id'] != gpu and retry['status'] == 'queued'


@pytest.mark.parametrize('step', [
    {'subcommand': 'bash'}, {'subcommand': 'mdrun', 'arguments': ['../escape']},
    {'subcommand': 'mdrun', 'arguments': ['a;id']},
])
def test_reject_execution_injection(client, admin_secret, step):
    result = client.post('/api/v2/tasks', headers={'Authorization': 'Bearer ' + admin_secret},
                         json={'name': 'invalid', 'steps': [step]})
    assert result.status_code == 422
