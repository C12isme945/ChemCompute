"""Verify native console and local controls using an isolated packaged instance."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chemcompute import desktop_backend as backend


def main():
    executable = str(Path(sys.argv[1]).resolve())
    original_cwd = Path.cwd()
    original_executable = sys.executable
    original_home = os.environ.get('CHEMCOMPUTE_HOME')
    try:
        with tempfile.TemporaryDirectory(prefix='chemcompute-controls-', ignore_cleanup_errors=True) as folder:
            os.environ['CHEMCOMPUTE_HOME'] = folder
            os.chdir(folder)
            root = Path(folder)
            (root / 'config').mkdir()
            (root / 'role.json').write_text(json.dumps({'role': 'controller'}))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            (root / 'config/controller.yaml').write_text(f'host: 127.0.0.1\nport: {port}\n')
            sys.executable = executable
            sys.frozen = True
            try:
                backend.start()
                first = backend.running_process()
                assert first is not None
                backend.start()
                assert backend.running_process().pid == first.pid
                for _ in range(40):
                    if backend.snapshot()['online']:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError('Desktop backend never became reachable')
                subprocess.run([executable, 'console', '--smoke-test'], check=True, timeout=30)
                assert backend.running_process().pid == first.pid, 'Closing console stopped backend'
                backend.restart()
                assert backend.running_process().pid != first.pid
                backend.stop()
                assert backend.running_process() is None
                print('PASS: desktop controls start, idempotent start, status, native window close, restart, stop')
            finally:
                backend.stop()
                sys.executable = original_executable
                del sys.frozen
                os.chdir(original_cwd)
    finally:
        if original_home is None:
            os.environ.pop('CHEMCOMPUTE_HOME', None)
        else:
            os.environ['CHEMCOMPUTE_HOME'] = original_home


if __name__ == '__main__':
    main()
