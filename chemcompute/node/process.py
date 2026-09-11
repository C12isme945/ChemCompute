"""Drain output continuously while retaining only a bounded prefix in memory."""
import subprocess
import threading
import time


def run_capped(cmd, cwd=None, timeout=300, cancel_event=None, **kwargs):
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, shell=False)
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
    try:
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise InterruptedError('Computation cancelled')
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(0.1)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        for thread in threads:
            thread.join(timeout=2)
    return subprocess.CompletedProcess(cmd, proc.returncode,
                                       outputs[0].decode('utf-8', errors='replace'),
                                       outputs[1].decode('utf-8', errors='replace'))
