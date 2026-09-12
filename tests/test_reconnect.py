import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx

from chemcompute.node import task_worker
from chemcompute.node.adapters.gromacs import ExecutionResult
from chemcompute.tasks import TaskStore
from tests.test_tasks_v2 import node


def test_parallel_nodes_claim_different_replicas(client, admin_secret):
    admin = {'Authorization': 'Bearer ' + admin_secret}
    first, second = node(client, admin), node(client, admin)
    batch = client.post('/api/v2/batches', headers=admin, json={'count': 2, 'task': {
        'name': 'replica', 'steps': [{'subcommand': 'version'}],
    }}).json()
    assert len(batch['tasks']) == 2
    def claim(worker):
        identity, auth = worker
        return client.post(f'/api/v2/worker/{identity}/claim', headers=auth).json()
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, [first, second]))
    assert len({value['id'] for value in claims}) == 2
    identity, auth = first
    auth['X-Lease-Token'] = claims[0]['lease_token']
    store = TaskStore(client.app.state.db.db_path)
    store.recovery.expire(now=10**12)
    assert store.get(claims[0]['id'])['status'] == 'recovering'
    assert client.post(f'/api/v2/worker/{identity}/claim', headers=auth).json() is None
    renewed = client.post(f"/api/v2/worker/{identity}/tasks/{claims[0]['id']}/renew", headers=auth)
    assert renewed.status_code == 200 and renewed.json()['status'] == 'running'
    auth['X-Lease-Token'] = 'obsolete-token'
    assert client.post(f"/api/v2/worker/{identity}/tasks/{claims[0]['id']}/renew", headers=auth).status_code == 409


def test_network_failure_replays_results_without_reexecuting(client, admin_secret, tmp_path, monkeypatch):
    admin = {'Authorization': 'Bearer ' + admin_secret}
    identity, auth = node(client, admin)
    client.post('/api/v2/tasks', headers=admin, json={'name': 'network test', 'steps': [{'subcommand': 'version'}]})
    task = client.post(f'/api/v2/worker/{identity}/claim', headers=auth).json()
    calls = []
    class Adapter:
        _wsl = False
        def __init__(self, *args):
            pass
        def probe(self):
            return None
        def run_bounded(self, *args, **kwargs):
            calls.append(args)
            return ExecutionResult(0, 'version complete', '', .1)
    monkeypatch.setattr(task_worker, 'GromacsAdapter', Adapter)
    config = SimpleNamespace(node_id=identity, workspace_dir=str(tmp_path / 'worker'),
                             controller_url='http://testserver', gromacs_custom_path=None,
                             max_job_timeout_seconds=30)
    agent = SimpleNamespace(config=config, running=True)
    class Connection:
        headers = auth
        failed = False
        def post(self, url, **kwargs):
            if url.endswith('/progress') and kwargs.get('json', {}).get('status') == 'running' and not self.failed:
                self.failed = True
                raise httpx.ConnectError('simulated disconnection')
            return client.post(url, headers={**auth, **kwargs.pop('headers', {})}, **kwargs)
        def put(self, url, **kwargs):
            return client.put(url, headers={**auth, **kwargs.pop('headers', {})}, **kwargs)
    connection = Connection()
    task_worker.execute(agent, connection, task)
    state_file = tmp_path / 'worker' / task['id'] / 'worker-state.json'
    assert not json.loads(state_file.read_text())['done']
    # Re-enter as after a controller reconnect / agent restart using only persisted state.
    task_worker.execute(agent, connection, task)
    assert json.loads(state_file.read_text())['done']
    assert len(calls) == 1
    assert client.get('/api/v2/tasks/' + task['id'], headers=admin).json()['status'] == 'completed'


def test_checkpoint_required_and_preserves_parameters(tmp_path):
    from chemcompute.tasks import Step
    step = Step(subcommand='mdrun', arguments=['-s', 'run.tpr', '-deffnm', 'run', '-nt', '2'])
    try:
        task_worker.resume_arguments(step, tmp_path)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Missing checkpoint must not silently restart MD')
    (tmp_path / 'run.cpt').write_bytes(b'checkpoint fixture')
    args = task_worker.resume_arguments(step, tmp_path)
    assert args == step.arguments + ['-cpi', 'run.cpt']
