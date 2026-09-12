import json

import httpx
import pytest

from chemcompute import deepseek
from chemcompute.tasks import TaskSpec


@pytest.mark.parametrize('selection,finish,valid', [('eligible', 'stop', True), ('invented', 'stop', False), ('offline', 'stop', False), ('eligible', 'length', False)])
def test_allocation_validation(monkeypatch, selection, finish, valid):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'mock-only-key')
    candidates = [{'node_id': identity, 'status': status,
                   'hardware_info': {'cpu_count_logical': 4, 'ram_available_mb': 8192},
                   'software_info': {'gromacs': {'found': True}}}
                  for identity, status in [('eligible', 'online'), ('offline', 'offline')]]
    def respond(request):
        payload = json.loads(request.content)
        metadata = json.loads(payload['messages'][1]['content'])
        assert metadata['package_filenames'] == ['test.gro']
        assert [n['node_id'] for n in metadata['candidates']] == ['eligible']
        assert 'package_bytes' not in metadata
        return httpx.Response(200, json={'choices': [{'finish_reason': finish,
            'message': {'content': json.dumps({'node_id': selection, 'reason': 'test'})}}]})
    client_type = httpx.Client
    monkeypatch.setattr(deepseek.httpx, 'Client', lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    spec = TaskSpec(name='test', steps=[{'subcommand': 'version'}])
    if valid:
        assert deepseek.recommend(spec, candidates, ['test.gro'])['node_id'] == 'eligible'
    else:
        with pytest.raises(RuntimeError, match='invalid allocation'):
            deepseek.recommend(spec, candidates, ['test.gro'])
