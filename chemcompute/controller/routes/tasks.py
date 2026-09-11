"""Authenticated package transfers and per-node atomic task queue."""
from __future__ import annotations

import json
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from chemcompute.controller.routes.auth import get_db, verify_admin
from chemcompute.packages import MAX_ARCHIVE_BYTES, inspect_package, sha256_file
from chemcompute.tasks import TaskSpec, TaskStore

router = APIRouter(prefix='/api/v2', tags=['desktop-tasks'])


def store(request: Request):
    return TaskStore(get_db(request).db_path)


def node_auth(request, node_id, authorization):
    if not authorization or not authorization.startswith('Bearer ') or not get_db(request).verify_node_token(node_id, authorization[7:]):
        raise HTTPException(401, 'Invalid node authentication')


def get_task(request, identity):
    task = store(request).get(identity)
    if task is None:
        raise HTTPException(404, 'Task not found')
    return task


async def receive_zip(request, target):
    temporary = target.with_suffix('.upload-' + uuid.uuid4().hex)
    total = 0
    try:
        with temporary.open('wb') as stream:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, 'Archive exceeds 100 MB limit')
                stream.write(chunk)
        info = inspect_package(temporary)
        info['sha256'] = sha256_file(temporary)
        temporary.replace(target)
        return info
    except HTTPException:
        raise
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)


@router.post('/packages', dependencies=[Depends(verify_admin)])
async def upload_package(request: Request):
    data = store(request)
    identity = 'pkg-' + uuid.uuid4().hex
    info = await receive_zip(request, data.root / (identity + '.zip'))
    with data.connect() as db:
        db.execute('INSERT INTO packages_v2 VALUES (?,?)', (identity, json.dumps(info)))
    return {'id': identity, **info}


@router.get('/packages', dependencies=[Depends(verify_admin)])
def list_packages(request: Request):
    with store(request).connect() as db:
        return [{'id': r['id'], **json.loads(r['info'])} for r in db.execute('SELECT * FROM packages_v2')]


@router.post('/tasks', dependencies=[Depends(verify_admin)])
def create_task(spec: TaskSpec, request: Request):
    data = store(request)
    if spec.node_id and not get_db(request).get_node(spec.node_id):
        raise HTTPException(400, 'Selected node no longer exists')
    if spec.package_id:
        with data.connect() as db:
            if not db.execute('SELECT id FROM packages_v2 WHERE id=?', (spec.package_id,)).fetchone():
                raise HTTPException(400, 'Unknown package')
    elif any(step.subcommand != 'version' for step in spec.steps):
        raise HTTPException(400, 'Upload an input package for computation')
    return data.create(spec)


@router.get('/tasks', dependencies=[Depends(verify_admin)])
def list_tasks(request: Request):
    return store(request).list()


@router.get('/tasks/{identity}', dependencies=[Depends(verify_admin)])
def task_detail(identity: str, request: Request):
    return get_task(request, identity)


@router.post('/tasks/{identity}/cancel', dependencies=[Depends(verify_admin)])
def cancel(identity: str, request: Request):
    data = store(request)
    get_task(request, identity)
    with data.connect() as db:
        db.execute("UPDATE tasks_v2 SET cancelled=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('queued','running')", (identity,))
    return data.get(identity)


@router.post('/tasks/{identity}/retry', dependencies=[Depends(verify_admin)])
def retry(identity: str, request: Request):
    previous = get_task(request, identity)
    if previous['status'] not in {'failed', 'cancelled', 'completed'}:
        raise HTTPException(409, 'Only retry a finished task; a disconnected worker may still be computing')
    return store(request).create(TaskSpec(**previous['spec']))


@router.get('/tasks/{identity}/results', dependencies=[Depends(verify_admin)])
def results(identity: str, request: Request):
    task = get_task(request, identity)
    path = store(request).root / (identity + '-results.zip')
    if not task['result_hash'] or not path.exists():
        raise HTTPException(404, 'No result archive uploaded yet')
    return FileResponse(path, filename=identity + '-results.zip', headers={'X-SHA256': task['result_hash']})


@router.post('/worker/{node_id}/claim')
def claim(node_id: str, request: Request, authorization: Annotated[str | None, Header()] = None):
    node_auth(request, node_id, authorization)
    return store(request).claim(get_db(request).get_node(node_id))


@router.get('/worker/{node_id}/tasks/{identity}')
def worker_status(node_id: str, identity: str, request: Request, authorization: Annotated[str | None, Header()] = None):
    node_auth(request, node_id, authorization)
    task = get_task(request, identity)
    if task['node'] != node_id:
        raise HTTPException(403, 'Task assigned to another node')
    return {'cancelled': bool(task['cancelled']), 'status': task['status']}


@router.get('/worker/{node_id}/tasks/{identity}/input')
def worker_input(node_id: str, identity: str, request: Request, authorization: Annotated[str | None, Header()] = None):
    worker_status(node_id, identity, request, authorization)
    package_id = get_task(request, identity)['spec']['package_id']
    if not package_id:
        raise HTTPException(404, 'No input package')
    data = store(request)
    with data.connect() as db:
        info = json.loads(db.execute('SELECT info FROM packages_v2 WHERE id=?', (package_id,)).fetchone()[0])
    return FileResponse(data.root / (package_id + '.zip'), headers={'X-SHA256': info['sha256']})


@router.put('/worker/{node_id}/tasks/{identity}/results')
async def worker_results(node_id: str, identity: str, request: Request, authorization: Annotated[str | None, Header()] = None):
    worker_status(node_id, identity, request, authorization)
    data = store(request)
    if data.get(identity)['status'] != 'running':
        raise HTTPException(409, 'Task is no longer running')
    info = await receive_zip(request, data.root / (identity + '-results.zip'))
    with data.connect() as db:
        db.execute('UPDATE tasks_v2 SET result_hash=? WHERE id=?', (info['sha256'], identity))
    return info


class Progress(BaseModel):
    status: Literal['running', 'completed', 'failed', 'cancelled']
    progress: int = Field(0, ge=0, le=100)
    log: str = Field('', max_length=500_000)


@router.post('/worker/{node_id}/tasks/{identity}/progress')
def progress(node_id: str, identity: str, payload: Progress, request: Request, authorization: Annotated[str | None, Header()] = None):
    worker_status(node_id, identity, request, authorization)
    data = store(request)
    with data.connect() as db:
        row = db.execute('SELECT status,result_hash FROM tasks_v2 WHERE id=?', (identity,)).fetchone()
        if row['status'] != 'running':
            raise HTTPException(409, 'Task already finished')
        if payload.status == 'completed' and not row['result_hash']:
            raise HTTPException(409, 'Upload results before completing task')
        db.execute('UPDATE tasks_v2 SET status=?,progress=?,log=?,updated=? WHERE id=?',
                   (payload.status, payload.progress, payload.log, time.time(), identity))
    return {'ok': True}
