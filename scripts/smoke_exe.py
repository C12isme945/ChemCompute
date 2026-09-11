"""End-to-end test against a real packaged controller and local node."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    exe = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix='chemcompute-smoke-') as folder:
        root = Path(folder)
        env = dict(os.environ, CHEMCOMPUTE_HOME=folder)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["PATH"] = os.pathsep.join([str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        ini = root / 'setup.ini'
        ini.write_text('[setup]\nrole=both\nname=smoke-node\nhost=127.0.0.1\n', encoding='utf-8')
        subprocess.run([exe, 'onboard', str(ini)], env=env, check=True, timeout=60)
        import yaml
        config_path = root / 'config/controller.yaml'
        cfg = yaml.safe_load(config_path.read_text('utf-8'))
        cfg['port'] = port
        config_path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
        node_path = root / 'config/node.yaml'
        node_cfg = yaml.safe_load(node_path.read_text('utf-8'))
        node_cfg['controller_url'] = f'http://127.0.0.1:{port}'
        node_path.write_text(yaml.safe_dump(node_cfg), encoding='utf-8')
        with (root / 'process.log').open('w') as log:
            proc = subprocess.Popen([exe, 'desktop-run'], env=env, stdout=log, stderr=log)
            try:
                for _ in range(60):
                    if proc.poll() is not None:
                        raise RuntimeError('Packaged program exited early')
                    secret = root / 'data/chemcompute-admin.secret'
                    if secret.exists():
                        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/nodes',
                              headers={'Authorization': 'Bearer ' + secret.read_text('utf-8').strip()})
                        try:
                            with urllib.request.urlopen(req, timeout=2) as response:
                                nodes = json.load(response)
                            if nodes and nodes[0]['status'] == 'online':
                                print('PASS: packaged controller, onboarding, local node heartbeat, authenticated inventory')
                                return
                        except OSError:
                            pass
                    time.sleep(1)
                raise RuntimeError('Packaged node did not come online within 60 seconds')
            finally:
                proc.terminate()
                proc.wait(timeout=10)


if __name__ == '__main__':
    main()
