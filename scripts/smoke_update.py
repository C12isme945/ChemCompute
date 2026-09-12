"""Real isolated Inno upgrade via the independent helper, with data preservation."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chemcompute.online_update import process_record
from chemcompute.update_maintenance import prepare_install


def wait_for(callback, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except (httpx.HTTPError, OSError, ValueError):
            pass
        time.sleep(.5)
    raise TimeoutError('Upgrade smoke condition timed out')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--installer', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    # Baseline 0.3 used a random TCP singleton port. Avoid Windows excluded
    # ranges when testing that historical binary (0.4 uses a named mutex).
    for _ in range(30):
        root = Path(tempfile.mkdtemp(prefix='chemcompute-upgrade-')).resolve()
        home, app = root/'runtime', root/'app'
        old_port = 40000 + int(hashlib.sha256(str(home).encode()).hexdigest()[:4], 16) % 20000
        with socket.socket() as guard:
            try:
                guard.bind(('127.0.0.1', old_port))
                break
            except OSError:
                continue
    else:
        raise RuntimeError('No usable singleton port for historical baseline')
    home.mkdir()
    environment = dict(os.environ, CHEMCOMPUTE_HOME=str(home))
    setup_args = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TESTINSTALL=1', '/DIR='+str(app)]
    subprocess.run([str(args.baseline.resolve()), *setup_args], env=environment, check=True, timeout=120)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    for name in ('controller', 'node'):
        path = home/f'config/{name}.yaml'
        cfg = yaml.safe_load(path.read_text('utf-8'))
        cfg.update({'port': port} if name == 'controller' else {'controller_url': f'http://127.0.0.1:{port}'})
        path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    exe = app/'ChemCompute.exe'
    backend = subprocess.Popen([str(exe), 'desktop-run'], env=environment)
    gui = None
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5) as client:
            wait_for(lambda: client.get('/openapi.json').status_code == 200)
            assert client.get('/openapi.json').json()['info']['version'] == '0.3.0'
            tracked = [home/'role.json', home/'config/controller.yaml', home/'config/node.yaml', home/'data/chemcompute-admin.secret']
            wait_for(lambda: all(p.is_file() for p in tracked))
            # Enrollment can update node.yaml; wait for a node before snapshot.
            secret = (home/'data/chemcompute-admin.secret').read_text().strip()
            client.headers['Authorization'] = 'Bearer '+secret
            wait_for(lambda: client.get('/api/v1/nodes').json())
            before = {p: p.read_bytes() for p in tracked}
            marker = home/'data/workspace/preserved-result.txt'
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text('retain this result', encoding='utf-8')
            ticket = prepare_install(home, ttl_seconds=3600)
            gui = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])
            installer = args.installer.resolve()
            request = {'home': str(home), 'executable': str(exe), 'installer': str(installer),
                       'sha256': hashlib.sha256(installer.read_bytes()).hexdigest(), 'version': '0.4.0',
                       'ticket': ticket, 'gui': process_record(psutil.Process(gui.pid)),
                       'backend': process_record(psutil.Process(backend.pid)), 'test_install': True}
            folder = home/'updates/install-smoke'
            folder.mkdir(parents=True)
            request_path = folder/'request.json'
            request_path.write_text(json.dumps(request), encoding='utf-8')
            powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
            helper = subprocess.Popen([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                       str(repo/'chemcompute/install_update.ps1'), '-RequestPath', str(request_path)], env=environment)
            assert helper.wait(timeout=180) == 0, (folder/'status.json').read_text('utf-8-sig')
            wait_for(lambda: client.get('/openapi.json').json()['info']['version'] == '0.4.0')
            for path, data in before.items():
                assert path.read_bytes() == data, 'Configuration changed: '+path.name
            assert marker.read_text() == 'retain this result'
            assert client.get('/api/v1/nodes').status_code == 200
            client.headers.pop('Authorization')
            assert client.get('/api/v1/nodes').status_code == 401
            print('PASS: independent helper, real 0.3 -> 0.4 installation, restart, authentication and retained configuration/results')
    finally:
        for process in psutil.process_iter(['exe']):
            try:
                if process.info['exe'] and Path(process.info['exe']).resolve() == exe.resolve():
                    process.terminate()
                    process.wait(timeout=10)
            except psutil.Error:
                pass
        if gui and gui.poll() is None:
            gui.terminate()
        uninstaller = app/'unins000.exe'
        if uninstaller.exists():
            subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], env=environment, timeout=120)
        print('Upgrade evidence retained at', root)


if __name__ == '__main__':
    main()
