"""Real controller outage + agent restart + GROMACS checkpoint continuation."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    command = [str(args.exe.resolve())] if args.exe else [sys.executable, str(repo / 'launcher.py')]
    root = Path(tempfile.mkdtemp(prefix='chemcompute-reconnect-')).resolve()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    processes = []
    logs = []
    def setup(name, role, invite=''):
        home = root / name
        home.mkdir()
        ini = home / 'setup.ini'
        ini.write_text(f'[setup]\nrole={role}\nname={name}\nurl={base}\ninvite={invite}\n', encoding='utf-8')
        subprocess.run([*command, 'onboard', str(ini)], env=dict(os.environ, CHEMCOMPUTE_HOME=str(home)), check=True, timeout=30)
        if role == 'controller':
            path = home / 'config/controller.yaml'
            cfg = yaml.safe_load(path.read_text('utf-8'))
            cfg['port'] = port
            path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
        return home
    def start(home):
        log = (home / f'process-{len(logs)}.log').open('w')
        logs.append(log)
        process = subprocess.Popen([*command, 'desktop-run'], env=dict(os.environ, CHEMCOMPUTE_HOME=str(home)), stdout=log, stderr=log)
        processes.append(process)
        return process
    def wait_until(check, seconds=60):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            try:
                value = check()
                if value:
                    return value
            except (httpx.HTTPError, OSError, ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError('Timed out; inspect isolated test logs at ' + str(root))
    try:
        controller_home = setup('controller', 'controller')
        controller = start(controller_home)
        secret = controller_home / 'data/chemcompute-admin.secret'
        wait_until(secret.exists)
        with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + secret.read_text('utf-8').strip()}, timeout=5) as client:
            wait_until(lambda: client.get('/api/v1/nodes').status_code == 200)
            invite = client.post('/api/v1/invites', json={}).json()['invite_code']
            node_home = setup('worker', 'node', invite)
            worker = start(node_home)
            wait_until(lambda: any(n['software_info']['gromacs']['found'] for n in client.get('/api/v1/nodes').json()))
            package = root / 'input.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                for path in (repo / 'examples/water-smoke').iterdir():
                    archive.write(path, path.name)
            uploaded = client.post('/api/v2/packages', content=package.read_bytes()).json()
            steps = uploaded['manifest']['steps']
            steps[-1]['arguments'] += ['-nsteps', '100000000', '-cpt', '0.01', '-maxh', '0.0026']
            response = client.post('/api/v2/tasks', json={'name': 'checkpoint restart verification', 'package_id': uploaded['id'], 'steps': steps, 'timeout_seconds': 180})
            response.raise_for_status()
            task_id = response.json()['id']
            task_home = node_home / 'data/workspace' / task_id
            checkpoint = task_home / 'files/run.cpt'
            wait_until(lambda: checkpoint.exists() and checkpoint.stat().st_size > 0)
            controller.terminate()
            controller.wait(timeout=10)
            worker.terminate()
            worker.wait(timeout=10)
            print('Controller disconnected and node agent stopped after real checkpoint creation', flush=True)
            controller = start(controller_home)
            wait_until(lambda: client.get('/api/v1/nodes').status_code == 200)
            worker = start(node_home)
            def completed():
                task = client.get('/api/v2/tasks/' + task_id).json()
                if task['status'] == 'failed':
                    raise AssertionError(task['log'])
                return task if task['status'] == 'completed' else None
            final = wait_until(completed, 120)
            assert '-cpi run.cpt' in final['log'] and 'continuing from step' in final['log']
            state = json.loads((task_home / 'worker-state.json').read_text('utf-8'))
            assert state['done'] and state['next_step'] == 2
            assert client.get('/api/v2/tasks/' + task_id + '/results').status_code == 200
            print('PASS: real controller outage, agent restart, checkpoint continuation and result replay', flush=True)
            print('Test evidence retained at ' + str(root))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
