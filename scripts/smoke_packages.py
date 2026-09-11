"""Real GROMACS package round-trip. Requires a usable native or WSL GROMACS."""
import argparse
import hashlib
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
    with tempfile.TemporaryDirectory(prefix='chemcompute-package-smoke-') as folder:
        root = Path(folder)
        env = dict(os.environ, CHEMCOMPUTE_HOME=folder)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        ini = root / 'setup.ini'
        ini.write_text('[setup]\nrole=both\nname=package-smoke\nhost=127.0.0.1\n', encoding='utf-8')
        subprocess.run([*command, 'onboard', str(ini)], env=env, check=True, timeout=60)
        for name in ['controller', 'node']:
            path = root / f'config/{name}.yaml'
            cfg = yaml.safe_load(path.read_text('utf-8'))
            cfg.update({'port': port} if name == 'controller' else {'controller_url': f'http://127.0.0.1:{port}', 'heartbeat_interval_seconds': 2})
            path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
        with (root / 'process.log').open('w') as log:
            proc = subprocess.Popen([*command, 'desktop-run'], env=env, stdout=log, stderr=log)
            try:
                secret = root / 'data/chemcompute-admin.secret'
                deadline = time.monotonic() + 90
                while not secret.exists() and time.monotonic() < deadline:
                    time.sleep(.5)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'Authorization': 'Bearer ' + secret.read_text('utf-8').strip()}, timeout=20) as client:
                    while time.monotonic() < deadline:
                        try:
                            nodes = client.get('/api/v1/nodes').json()
                            if nodes and nodes[0]['software_info']['gromacs']['found']:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(1)
                    else:
                        raise RuntimeError('No real GROMACS node available')
                    package = root / 'input.zip'
                    with zipfile.ZipFile(package, 'w') as archive:
                        for path in (repo / 'examples/water-smoke').iterdir():
                            archive.write(path, path.name)
                    response = client.post('/api/v2/packages', content=package.read_bytes())
                    response.raise_for_status()
                    uploaded = response.json()
                    assert uploaded['sha256'] == hashlib.sha256(package.read_bytes()).hexdigest()
                    manifest = uploaded['manifest']
                    response = client.post('/api/v2/tasks', json={'name': 'Real water smoke', 'package_id': uploaded['id'], 'steps': manifest['steps'], 'timeout_seconds': 180})
                    response.raise_for_status()
                    identity = response.json()['id']
                    deadline = time.monotonic() + 240
                    while time.monotonic() < deadline:
                        task = client.get('/api/v2/tasks/' + identity).json()
                        if task['status'] in {'failed', 'cancelled'}:
                            raise RuntimeError(task['log'])
                        if task['status'] == 'completed':
                            break
                        time.sleep(1)
                    else:
                        raise RuntimeError('Package task did not finish')
                    response = client.get('/api/v2/tasks/' + identity + '/results')
                    response.raise_for_status()
                    assert hashlib.sha256(response.content).hexdigest() == response.headers['X-SHA256']
                    result = root / 'result.zip'
                    result.write_bytes(response.content)
                    with zipfile.ZipFile(result) as archive:
                        assert {'run.tpr', 'run.gro', 'run.log', 'chemcompute-execution.log'} <= set(archive.namelist())
                        assert 'Finished mdrun' in archive.read('run.log').decode(errors='replace')
                    print('PASS: real GROMACS input upload, atomic assignment, grompp + mdrun, result upload/download and SHA-256')
            finally:
                proc.terminate()
                proc.wait(timeout=15)


if __name__ == '__main__':
    main()
