import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from chemcompute.controller.db import Database
from chemcompute.tasks import Step, TaskSpec, TaskStore
from chemcompute.update_maintenance import (
    MaintenanceBusy,
    finish_install,
    local_intake_paused,
    prepare_install,
)


@pytest.fixture
def runtime(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'role.json').write_text('{"role":"controller"}')
    (tmp_path / 'config/controller.yaml').write_text('db_path: data/chemcompute.db\n')
    database = Database(tmp_path / 'data/chemcompute.db')
    store = TaskStore(database.db_path)
    database.register_node('node', 'Node', 'hash', None, 'host', 'test', '3', {}, {})
    return tmp_path, database, store


def spec():
    return TaskSpec(name='test', steps=[Step(subcommand='version')])


def test_gate_blocks_both_api_generations_and_releases(runtime):
    home, database, store = runtime
    ticket = prepare_install(home)
    assert local_intake_paused(home)
    for action in [lambda: store.create(spec()), lambda: store.claim({'node_id': 'node'}),
                   lambda: database.create_job('job', 'node', 'version', []),
                   lambda: database.mark_job_running('job', 'node'),
                   lambda: database.get_pending_jobs_for_node('node')]:
        with pytest.raises(MaintenanceBusy) as error:
            action()
        assert error.value.status_code == 503
    assert not finish_install(dict(ticket, token='wrong'))
    assert finish_install(ticket)
    assert not local_intake_paused(home)
    assert store.create(spec())['status'] == 'queued'


@pytest.mark.parametrize('status', ['queued', 'running', 'recovering', 'unknown'])
def test_any_active_or_unknown_task_blocks_update(runtime, status):
    home, _, store = runtime
    task = store.create(spec())
    with store.connect() as conn:
        conn.execute('UPDATE tasks_v2 SET status=? WHERE id=?', (status, task['id']))
    with pytest.raises(MaintenanceBusy):
        prepare_install(home)


def test_old_active_job_hidden_by_newer_500_records_still_blocks(runtime):
    home, database, _ = runtime
    database.create_job('old-active', 'node', 'version', [])
    with database._get_connection() as conn:
        conn.executemany("INSERT INTO jobs(job_id,node_id,subcommand,arguments_json,status,created_at) VALUES(?,'node','version','[]','completed','2099')", [(f'done-{i}',) for i in range(501)])
    with pytest.raises(MaintenanceBusy):
        prepare_install(home)


@pytest.mark.parametrize('state', [None, {'done': False}, {'done': 'true'}])
def test_unfinished_or_missing_local_spool_blocks(runtime, state):
    home, _, _ = runtime
    folder = home / 'data/workspace/task-local'
    folder.mkdir(parents=True)
    if state is not None:
        (folder / 'worker-state.json').write_text(json.dumps(state))
    with pytest.raises(MaintenanceBusy):
        prepare_install(home)


def test_expiry_and_stale_ticket_cannot_clear_new_gate(runtime):
    home, database, _ = runtime
    first = prepare_install(home)
    with database._get_connection() as conn:
        conn.execute('UPDATE update_maintenance SET expires_at=0')
    second = prepare_install(home)
    assert not finish_install(first)
    assert local_intake_paused(home)
    assert finish_install(second)


def test_concurrent_submission_and_prepare_are_serialized(runtime):
    home, _, store = runtime
    barrier = threading.Barrier(2)

    def action(callback):
        barrier.wait(timeout=5)
        try:
            return callback()
        except MaintenanceBusy:
            return None

    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(action, lambda: prepare_install(home))
        submission = pool.submit(action, lambda: store.create(spec()))
        ticket, task = pending.result(), submission.result()
    assert (ticket is None) != (task is None)
    if ticket:
        assert store.list() == []
        finish_install(ticket)


@pytest.mark.parametrize('role', ['node', 'unknown'])
def test_unsupported_role_fails_closed(runtime, role):
    home, _, _ = runtime
    (home / 'role.json').write_text(json.dumps({'role': role}))
    with pytest.raises(MaintenanceBusy):
        prepare_install(home)


def test_missing_and_incompatible_db_fail_closed(runtime):
    home, _, _ = runtime
    (home / 'config/controller.yaml').write_text('db_path: data/missing.db\n')
    with pytest.raises(MaintenanceBusy):
        prepare_install(home)
    assert not (home / 'data/missing.db').exists()
    with sqlite3.connect(home / 'data/missing.db') as conn:
        conn.executescript('CREATE TABLE nodes(node_id TEXT); CREATE TABLE jobs(status TEXT);')
    with pytest.raises(MaintenanceBusy):
        prepare_install(home)
