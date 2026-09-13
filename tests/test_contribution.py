import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import psutil
import pytest

from chemcompute.common.models import GromacsSoftwareInfo, HardwareInfo
from chemcompute.contribution import (
    ContributionSettings,
    compute_budget,
    gromacs_arguments,
    load_settings,
    pin_process,
    save_settings,
    validate_gaussian_input,
)
from chemcompute.node import task_worker
from chemcompute.node.adapters.gaussian import GaussianAdapter
from chemcompute.node.adapters.gromacs import GromacsAdapter
from chemcompute.node.process import run_capped
from chemcompute.tasks import Resources, Step, TaskSpec, eligible


@pytest.fixture
def budget():
    return compute_budget(ContributionSettings(), HardwareInfo(cpu_count_logical=8, ram_total_mb=8192))


def test_atomic_preferences_do_not_touch_enrollment(tmp_path):
    path = tmp_path / 'node.yaml'
    path.write_text('retained-token-fixture')
    save_settings(path, ContributionSettings(cpu_percent=25, paused=True))
    assert load_settings(path).paused
    assert load_settings(path).cpu_percent == 25
    assert path.read_text() == 'retained-token-fixture'
    path.with_name('contribution.json').write_text('{bad json')
    assert load_settings(path).paused


def test_physical_telemetry_unchanged_and_rounding():
    hw = HardwareInfo(cpu_count_logical=6, ram_total_mb=8001)
    original = hw.model_dump()
    offer = compute_budget(ContributionSettings(cpu_percent=25), hw)
    assert offer['cpu_cores'] == 2 and offer['ram_mb'] == 4000
    assert hw.model_dump() == original


@pytest.mark.parametrize('change,resources,expected', [
    ({}, Resources(), True), ({'paused': True}, Resources(), False),
    ({}, Resources(cpu=5), False), ({}, Resources(ram_mb=4097), False),
    ({}, Resources(gpu=True), False), ({'gpu_enabled': True}, Resources(gpu=True), True),
])
def test_scheduler_respects_offer(budget, change, resources, expected):
    budget.update(change)
    node = {'status': 'online', 'hardware_info': {'cpu_count_logical': 8, 'ram_available_mb': 8192, 'gpus': [{}], 'contribution': budget},
            'software_info': {'gromacs': {'found': True, 'cuda_enabled': True}}}
    assert eligible(node, resources) == expected
    node['hardware_info']['contribution'] = {'ram_mb': 'invalid'}
    assert not eligible(node, resources)


@pytest.mark.parametrize('arguments', [['-nt', '8'], ['-ntomp', '0'], ['-nb', 'gpu'], ['-pme', 'auto'], ['-pin', 'on']])
def test_conflicting_compute_flags_rejected(budget, arguments):
    with pytest.raises(ValueError):
        gromacs_arguments('mdrun', arguments, budget)


def test_native_process_really_has_one_core(tmp_path):
    code = 'import time,psutil; time.sleep(.4); print(len(psutil.Process().cpu_affinity()))'
    result = run_capped([sys.executable, '-c', code], cwd=tmp_path, cpu_cores=1, timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == '1'


def test_constraint_failure_terminates_process(tmp_path, monkeypatch):
    import chemcompute.contribution as policy
    monkeypatch.setattr(policy, 'pin_process', Mock(side_effect=RuntimeError('Cannot pin')))
    with pytest.raises(RuntimeError, match='Cannot pin'):
        run_capped([sys.executable, '-c', 'import time; time.sleep(10)'], cwd=tmp_path, cpu_cores=1, timeout=5)


@pytest.mark.parametrize('exit_code', [None, 0])
def test_windows_teardown_access_denied_only_ignored_after_exit(monkeypatch, exit_code):
    import chemcompute.contribution as policy
    parent = Mock()
    parent.cpu_affinity.return_value = [0, 1]
    def process(pid=None):
        if pid is None:
            return parent
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(policy.psutil, 'Process', process)
    proc = Mock(pid=99)
    proc.poll.return_value = exit_code
    if exit_code is None:
        with pytest.raises(psutil.AccessDenied):
            pin_process(proc, 1)
    else:
        pin_process(proc, 1)


@pytest.mark.parametrize('wsl', [False, True])
def test_gromacs_launch_has_effective_cpu_and_gpu_controls(tmp_path, monkeypatch, budget, wsl):
    adapter = GromacsAdapter(workspace_dir=tmp_path)
    adapter._wsl = wsl
    monkeypatch.setattr(adapter, 'probe', lambda: GromacsSoftwareInfo(found=True, executable_path='wsl.exe' if wsl else 'gmx.exe'))
    launch = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    monkeypatch.setattr('chemcompute.node.process.run_capped', launch)
    assert adapter.run_bounded('mdrun', ['-s', 'input.tpr'], contribution_budget=budget).exit_code == 0
    cmd = launch.call_args.args[0]
    assert cmd[cmd.index('-nb')+1] == 'cpu'
    assert cmd[cmd.index('-pin')+1] == 'off'
    if wsl:
        assert cmd.index('taskset') < cmd.index('gmx')
        assert '0-3' in cmd and 'CUDA_VISIBLE_DEVICES=-1' in cmd
        assert launch.call_args.kwargs['cpu_cores'] is None
    else:
        assert launch.call_args.kwargs['cpu_cores'] == 4
        assert launch.call_args.kwargs['env']['CUDA_VISIBLE_DEVICES'] == '-1'


@pytest.mark.parametrize('directive', ['%NProcShared=5', '%Mem=5GB', '%GPUCPU=0=0', '%CPU=0-7', '%LindaWorkers=remote'])
def test_gaussian_oversized_or_bypassing_input_is_unchanged(tmp_path, budget, directive):
    path = tmp_path / 'input.gjf'
    content = directive + '\n# HF/STO-3G\n\nfixture\n\n0 1\nH 0 0 0\n\n'
    path.write_text(content)
    with pytest.raises(ValueError):
        validate_gaussian_input(path, budget)
    assert path.read_text() == content


def test_gaussian_adapter_pins_real_standin_process(tmp_path, budget, monkeypatch):
    path = tmp_path / 'fixture.gjf'
    path.write_text("import time,psutil; time.sleep(.3); print('cores=' + str(len(psutil.Process().cpu_affinity()))); print('Normal termination of Gaussian')")
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    adapter._is_windows = True
    monkeypatch.setattr(adapter, 'probe', lambda: {'found': True, 'executable_path': sys.executable})
    budget['cpu_cores'] = 1
    result = adapter.run_bounded('run', ['fixture.gjf'], contribution_budget=budget)
    assert result.exit_code == 0 and 'cores=1' in result.stdout


@pytest.mark.parametrize('recovery', [False, True])
def test_paused_worker_preserves_recovery_without_new_claim(tmp_path, monkeypatch, recovery):
    agent = SimpleNamespace(config=SimpleNamespace(node_token='fixture', controller_url='http://fixture', node_id='fixture', workspace_dir=str(tmp_path)),
                            config_path=tmp_path / 'node.yaml', running=True, execution_lock=threading.Lock(), active_task=False)
    save_settings(agent.config_path, ContributionSettings(paused=True))
    if recovery:
        path = tmp_path / 'task-fixture'
        path.mkdir()
        (path / 'worker-state.json').write_text(json.dumps({'done': False, 'task': {'id': 'fixture'}}))
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(200, json=None)))
    monkeypatch.setattr(task_worker.httpx, 'Client', lambda **_: client)
    execute = Mock(side_effect=lambda *_: setattr(agent, 'running', False))
    monkeypatch.setattr(task_worker, 'execute', execute)
    monkeypatch.setattr(task_worker.time, 'sleep', lambda _: setattr(agent, 'running', False))
    task_worker.run_queue(agent)
    assert not calls
    assert execute.call_count == int(recovery)


def test_task_reconnect_keeps_original_budget(tmp_path, monkeypatch, budget):
    config = SimpleNamespace(controller_url='http://fixture', node_id='node', workspace_dir=str(tmp_path), gromacs_custom_path=None, max_job_timeout_seconds=60)
    agent = SimpleNamespace(config=config, config_path=tmp_path / 'node.yaml', running=True)
    current = dict(budget)
    monkeypatch.setattr(task_worker, 'snapshot', lambda _: dict(current))
    limits = []
    adapter = Mock(_wsl=False)
    adapter.run_bounded.side_effect = lambda *a, **kw: limits.append(kw['contribution_budget']['cpu_cores']) or SimpleNamespace(exit_code=0, stdout='', stderr='', error_message=None)
    monkeypatch.setattr(task_worker, 'GromacsAdapter', lambda *a: adapter)
    disconnected = False
    def handle(request):
        nonlocal disconnected
        if request.url.path.endswith('/progress') and not disconnected:
            disconnected = True
            raise httpx.ConnectError('fixture disconnection')
        return httpx.Response(200, json={'cancelled': False})
    task = {'id': 'task-fixture', 'lease_token': 'fixture', 'spec': TaskSpec(name='fixture', steps=[Step(subcommand='version'), Step(subcommand='version')]).model_dump()}
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        task_worker.execute(agent, client, task)
        current.update(cpu_cores=1, paused=True)
        task_worker.execute(agent, client, task)
    assert limits == [4, 4], 'Preferences must not change an already accepted task after reconnect'
    assert json.loads((tmp_path / 'task-fixture/worker-state.json').read_text())['done']


def test_claim_race_rejects_new_oversized_task(tmp_path, monkeypatch, budget):
    agent = SimpleNamespace(config=SimpleNamespace(controller_url='http://fixture', node_id='node', workspace_dir=str(tmp_path)), config_path=None)
    monkeypatch.setattr(task_worker, 'snapshot', lambda _: budget)
    launch = Mock()
    monkeypatch.setattr(task_worker, 'GromacsAdapter', launch)
    task = {'id': 'task-race', 'lease_token': 'fixture', 'spec': TaskSpec(name='fixture', resources=Resources(cpu=5), steps=[Step(subcommand='version')]).model_dump()}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'cancelled': False}))) as client:
        task_worker.execute(agent, client, task)
    launch.assert_not_called()
    state = json.loads((tmp_path / 'task-race/worker-state.json').read_text())
    assert state['done'] and state['terminal'] == 'failed'

