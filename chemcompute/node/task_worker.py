"""Durable per-node execution with lease renewal and reconnect replay."""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx
import psutil

from chemcompute.contribution import admits, snapshot
from chemcompute.node.adapters.gromacs import GromacsAdapter
from chemcompute.packages import MAX_ARCHIVE_BYTES, extract_package, pack_results, sha256_file
from chemcompute.tasks import TaskSpec

logger = logging.getLogger(__name__)


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def process_alive(path):
    if not path.exists():
        return False
    record = json.loads(path.read_text('utf-8'))
    if record.get('finished'):
        return False
    try:
        process = psutil.Process(record['pid'])
        return (abs(process.create_time()-record['created']) < .01
                and process.exe() == record['exe'] and process.cmdline() == record['argv'])
    except psutil.NoSuchProcess:
        return False


def wsl_busy(directory):
    # Fixed script, no interpolated filenames or user arguments. Check Linux cwd identity.
    script = 'for p in /proc/[0-9]*/cwd; do if [ "$p" -ef . ]; then c=$(cat "${p%/cwd}/comm" 2>/dev/null); case "$c" in gmx*|timeout) echo busy;; esac; fi; done'
    response = subprocess.run(['wsl.exe', '--cd', str(directory), '--exec', 'sh', '-c', script],
                              capture_output=True, text=True, timeout=15,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if response.returncode:
        raise RuntimeError('Cannot verify previous Linux computation has stopped')
    return 'busy' in response.stdout


def resume_arguments(step, directory):
    if step.subcommand != 'mdrun':
        raise RuntimeError('Interrupted non-MD step requires manual review before retry')
    args = list(step.arguments)
    def argument(flag, default):
        return args[args.index(flag)+1] if flag in args and args.index(flag)+1 < len(args) else default
    checkpoint = argument('-cpo', argument('-deffnm', 'state') + '.cpt')
    if not (directory / checkpoint).is_file() or not (directory / checkpoint).stat().st_size:
        raise RuntimeError('No usable GROMACS checkpoint; automatic restart withheld')
    if '-cpi' in args:
        args[args.index('-cpi')+1] = checkpoint
    else:
        args += ['-cpi', checkpoint]
    return args


def download(response, target):
    count = 0
    with target.open('wb') as output:
        for chunk in response.iter_bytes(65536):
            count += len(chunk)
            if count > MAX_ARCHIVE_BYTES:
                raise RuntimeError('Input download exceeds size limit')
            output.write(chunk)
    if sha256_file(target) != response.headers.get('X-SHA256'):
        raise RuntimeError('Input checksum mismatch')


def execute(agent, client, task):
    identity = task['id']
    spec = TaskSpec(**task['spec'])
    base = f'{agent.config.controller_url.rstrip("/")}/api/v2/worker/{agent.config.node_id}/tasks/{identity}'
    root = Path(agent.config.workspace_dir).resolve() / identity
    root.mkdir(parents=True, exist_ok=True)
    directory = root / 'files'
    state_path = root / 'worker-state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text('utf-8'))
        if state['task']['lease_token'] != task['lease_token']:
            raise RuntimeError('Conflicting attempt in local workspace')
    else:
        state = {'task': task, 'stage': 'input', 'next_step': 0, 'log': '', 'started': time.time(), 'done': False, 'contribution': snapshot(getattr(agent, 'config_path', None))}
        save(state_path, state)
    headers = {'X-Lease-Token': task['lease_token']}
    cancelled, finished = threading.Event(), threading.Event()
    def watch():
        with httpx.Client(headers=client.headers, timeout=8) as control:
            while not finished.wait(10):
                try:
                    response = control.post(base + '/renew', headers=headers)
                    if response.status_code == 409 or (response.is_success and response.json()['cancelled']):
                        cancelled.set()
                except httpx.HTTPError:
                    pass  # Continue locally; replay durable state when connected again.
    try:
        response = client.post(base + '/renew', headers=headers)
        if response.status_code == 409:
            state['done'] = True
            save(state_path, state)
            return
        response.raise_for_status()
        if response.json()['cancelled']:
            cancelled.set()
        threading.Thread(target=watch, daemon=True).start()
        if 'contribution' not in state:
            state['contribution'] = snapshot(getattr(agent, 'config_path', None))
            save(state_path, state)
        if state['stage'] == 'input':
            if not admits(spec.resources, state['contribution']):
                raise RuntimeError('Task exceeds current contribution or node is paused; adjust budget and retry')
            if spec.package_id:
                with client.stream('GET', base + '/input', headers=headers) as response:
                    response.raise_for_status()
                    download(response, root / 'input.zip')
                staged = root / ('input-staged-' + uuid.uuid4().hex)
                extract_package(root / 'input.zip', staged)
                if directory.exists():
                    directory.rename(root / ('input-interrupted-' + uuid.uuid4().hex))
                staged.rename(directory)
            else:
                directory.mkdir(exist_ok=True)
            state['stage'] = 'ready'
            save(state_path, state)
        if spec.software == 'gaussian':
            from chemcompute.node.adapters.gaussian import GaussianAdapter
            adapter = GaussianAdapter(agent.config.gaussian_custom_path, directory, agent.config.max_job_timeout_seconds)
        else:
            adapter = GromacsAdapter(agent.config.gromacs_custom_path, directory, agent.config.max_job_timeout_seconds)
            adapter.probe()
        if state['stage'] == 'executing':
            while process_alive(root / 'process.json') or (spec.software == 'gromacs' and adapter._wsl and wsl_busy(directory)):
                if not agent.running:
                    return
                time.sleep(3)
            if not spec.auto_resume or spec.software != 'gromacs':
                raise RuntimeError('Interrupted task requires manual restart; automatic scientific parameter changes disabled')
            record = json.loads((root / 'process.json').read_text('utf-8')) if (root / 'process.json').exists() else {}
            if record.get('finished'):
                if record.get('returncode') != 0:
                    raise RuntimeError('Previous computation exited unsuccessfully before recovery')
                state['next_step'] += 1
                state['stage'] = 'ready'
                save(state_path, state)
            else:
                state['resume'] = True
        while state['next_step'] < len(spec.steps) and state['stage'] != 'terminal':
            if cancelled.is_set():
                raise InterruptedError('Cancellation confirmed')
            step = spec.steps[state['next_step']]
            arguments = resume_arguments(step, directory) if state.pop('resume', False) else step.arguments
            remaining = int(spec.timeout_seconds - (time.time()-state['started']))
            if remaining < 1:
                raise TimeoutError('Task wall-time limit exceeded')
            state['stage'] = 'executing'
            save(state_path, state)
            options = {'contribution_budget': state['contribution'], 'timeout_seconds': remaining, 'cancel_event': cancelled, 'process_record': root / 'process.json'}
            result = adapter.run_bounded(step.subcommand, arguments, **options)
            state['log'] = (state['log'] + f"\nStep {state['next_step']+1}: {spec.software} {step.subcommand}\n" + result.stdout + '\n' + result.stderr + '\n' + (result.error_message or ''))[-490000:]
            (directory / 'chemcompute-execution.log').write_text(state['log'], encoding='utf-8')
            if cancelled.is_set():
                raise InterruptedError('Cancellation confirmed')
            if result.exit_code:
                raise RuntimeError(f'Step failed with exit {result.exit_code}')
            state['next_step'] += 1
            state['stage'] = 'ready'
            save(state_path, state)
            client.post(base + '/progress', headers=headers, json={'status': 'running', 'progress': int(state['next_step']*95/len(spec.steps)), 'log': state['log']}).raise_for_status()
        if state['stage'] != 'terminal':
            pack_results(directory, root / 'results.zip')
            state.update(stage='terminal', terminal='completed')
            save(state_path, state)
    except httpx.HTTPError:
        return  # Durable spool is replayed on the next poll/restart.
    except Exception as exc:
        state.update(stage='terminal', terminal='cancelled' if isinstance(exc, InterruptedError) else 'failed')
        state['log'] = (state['log'] + '\n' + str(exc))[-490000:]
        save(state_path, state)
    finally:
        finished.set()
    try:
        client.post(base + '/renew', headers=headers).raise_for_status()
        if state['terminal'] == 'completed':
            with (root / 'results.zip').open('rb') as stream:
                client.put(base + '/results', headers=headers, content=stream, timeout=120).raise_for_status()
        response = client.post(base + '/progress', headers=headers, json={'status': state['terminal'], 'progress': 100 if state['terminal']=='completed' else 0, 'log': state['log']})
        response.raise_for_status()
        state['done'] = True
        save(state_path, state)
    except httpx.HTTPError:
        logger.info('Task %s retained for reconnect replay', identity)


def run_queue(agent):
    headers = {'Authorization': f'Bearer {agent.config.node_token}', 'X-ChemCompute-Protocol': '3'}
    url = f'{agent.config.controller_url.rstrip("/")}/api/v2/worker/{agent.config.node_id}/claim'
    with httpx.Client(headers=headers, timeout=20) as client:
        while agent.running:
            try:
                with agent.execution_lock:
                    pending = []
                    for path in Path(agent.config.workspace_dir).glob('task-*/worker-state.json'):
                        value = json.loads(path.read_text('utf-8'))
                        if not value.get('done'):
                            pending.append(value['task'])
                    if pending:
                        task = pending[0]
                    elif snapshot(getattr(agent, 'config_path', None))['paused']:
                        task = None
                    else:
                        response = client.post(url)
                        if response.status_code == 404:
                            return
                        response.raise_for_status()
                        task = response.json()
                    if task:
                        agent.active_task = True
                        try:
                            execute(agent, client, task)
                        finally:
                            agent.active_task = False
            except Exception:
                logger.warning('Task recovery/polling unavailable; retrying')
            for _ in range(3):
                if not agent.running:
                    return
                time.sleep(1)
