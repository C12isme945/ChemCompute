"""Single-job worker, independent of hardware heartbeats."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import httpx

from chemcompute.node.adapters.gromacs import GromacsAdapter
from chemcompute.packages import MAX_ARCHIVE_BYTES, extract_package, pack_results, sha256_file
from chemcompute.tasks import TaskSpec

logger = logging.getLogger(__name__)


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
    root.mkdir(parents=True, exist_ok=False)
    directory = root / 'files'
    cancelled = threading.Event()
    finished = threading.Event()
    log = ''

    def watch():
        with httpx.Client(headers=client.headers, timeout=5) as control:
            while not finished.wait(2):
                try:
                    response = control.get(base)
                    response.raise_for_status()
                    if response.json()['cancelled'] or not agent.running:
                        cancelled.set()
                except httpx.HTTPError:
                    # A network outage must not claim successful cancellation.
                    pass

    threading.Thread(target=watch, daemon=True).start()
    try:
        if spec.package_id:
            with client.stream('GET', base + '/input') as response:
                response.raise_for_status()
                download(response, root / 'input.zip')
            extract_package(root / 'input.zip', directory)
        else:
            directory.mkdir()
        adapter = GromacsAdapter(agent.config.gromacs_custom_path, directory,
                                 min(spec.timeout_seconds, agent.config.max_job_timeout_seconds))
        started = time.monotonic()
        for index, step in enumerate(spec.steps):
            if cancelled.is_set():
                raise InterruptedError('Cancellation acknowledged by worker')
            remaining = int(spec.timeout_seconds - (time.monotonic() - started))
            if remaining < 1:
                raise TimeoutError('Task wall-time limit exceeded')
            result = adapter.run_bounded(step.subcommand, step.arguments,
                                         timeout_seconds=remaining, cancel_event=cancelled)
            log = (log + f'\nStep {index + 1}: gmx {step.subcommand}\n' + result.stdout + '\n' + result.stderr + '\n' + (result.error_message or ''))[-490_000:]
            (directory / 'chemcompute-execution.log').write_text(log, encoding='utf-8')
            if cancelled.is_set():
                raise InterruptedError('Cancellation acknowledged by worker')
            if result.exit_code != 0:
                raise RuntimeError(f'Step {index + 1} failed: exit {result.exit_code}')
            report = client.post(base + '/progress', json={'status': 'running', 'progress': int((index + 1) * 95 / len(spec.steps)), 'log': log})
            report.raise_for_status()
        pack_results(directory, root / 'results.zip')
        with (root / 'results.zip').open('rb') as stream:
            response = client.put(base + '/results', content=stream, headers={'Content-Type': 'application/zip'}, timeout=120)
            response.raise_for_status()
        response = client.post(base + '/progress', json={'status': 'completed', 'progress': 100, 'log': log})
        response.raise_for_status()
    except Exception as exc:
        state = 'cancelled' if isinstance(exc, InterruptedError) else 'failed'
        message = (log + '\n' + str(exc))[-490_000:]
        try:
            client.post(base + '/progress', json={'status': state, 'log': message}).raise_for_status()
        except httpx.HTTPError:
            (root / 'report-pending.txt').write_text(message, encoding='utf-8')
            logger.warning('Task %s result report unavailable; local files retained', identity)
    finally:
        finished.set()


def run_queue(agent):
    headers = {'Authorization': f'Bearer {agent.config.node_token}'}
    url = f'{agent.config.controller_url.rstrip("/")}/api/v2/worker/{agent.config.node_id}/claim'
    with httpx.Client(headers=headers, timeout=20) as client:
        while agent.running:
            try:
                # The same process cannot execute legacy and package jobs concurrently.
                with agent.execution_lock:
                    response = client.post(url)
                    if response.status_code == 404:
                        return  # Compatible with a v0.1 controller.
                    response.raise_for_status()
                    task = response.json()
                    if task:
                        agent.active_task = True
                        try:
                            execute(agent, client, task)
                        finally:
                            agent.active_task = False
            except Exception:
                logger.warning('Task polling unavailable; retrying')
            for _ in range(3):
                if not agent.running:
                    return
                time.sleep(1)
