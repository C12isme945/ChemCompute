"""Drain output continuously while retaining only a bounded prefix in memory."""
import json
import subprocess
import threading
import time
from pathlib import Path

import psutil


def run_capped(cmd, cwd=None, timeout=300, cancel_event=None, process_record=None, cpu_cores=None, env=None, **kwargs):
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, shell=False, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def save_process(value):
        if process_record:
            path = Path(process_record)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value), encoding='utf-8')
            temporary.replace(path)
    if process_record:
        try:
            identity = psutil.Process(proc.pid)
            save_process({'pid': proc.pid, 'created': identity.create_time(), 'exe': identity.exe(), 'argv': identity.cmdline(), 'finished': False})
        except psutil.NoSuchProcess:
            save_process({'finished': True, 'returncode': proc.wait()})
    outputs = [bytearray(), bytearray()]

    def drain(pipe, target):
        try:
            while chunk := pipe.read(8192):
                available = max(0, 500_000 - len(target))
                target.extend(chunk[:available])
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(pipe, target), daemon=True)
               for pipe, target in zip((proc.stdout, proc.stderr), outputs)]
    for thread in threads:
        thread.start()
    def terminate_tree():
        try:
            children = psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            children = []
        for child in reversed(children):
            try:
                child.kill()
            except psutil.Error:
                pass
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    try:
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if cpu_cores:
                from chemcompute.contribution import pin_process
                pin_process(proc, cpu_cores)
            if cancel_event and cancel_event.is_set():
                terminate_tree()
                raise InterruptedError('Computation cancelled')
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(0.1)
    except Exception:
        terminate_tree()
        raise
    finally:
        if proc.poll() is not None:
            save_process({'finished': True, 'returncode': proc.returncode})
        for thread in threads:
            thread.join(timeout=2)
    return subprocess.CompletedProcess(cmd, proc.returncode,
                                       outputs[0].decode('utf-8', errors='replace'),
                                       outputs[1].decode('utf-8', errors='replace'))
