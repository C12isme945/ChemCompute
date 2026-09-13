"""Validated task specifications and additive persistent queue."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

COMMANDS = {'version', 'check', 'grompp', 'mdrun', 'editconf', 'solvate', 'genion', 'energy', 'run'}


class Step(BaseModel):
    subcommand: str
    arguments: list[str] = Field(default_factory=list, max_length=100)

    @field_validator('subcommand')
    @classmethod
    def command_allowed(cls, value):
        if value not in COMMANDS:
            raise ValueError('Unsupported GROMACS command')
        return value

    @field_validator('arguments')
    @classmethod
    def arguments_safe(cls, values):
        for value in values:
            if len(value) > 512 or re.search(r'[;&|`$<>\r\n\x00:]|\.\.', value) or value.startswith(('/', '\\')):
                raise ValueError('Unsafe argument or external path')
        return values


class Resources(BaseModel):
    cpu: int = Field(1, ge=1, le=256)
    ram_mb: int = Field(256, ge=64, le=4_194_304)
    gpu: bool = False


class TaskSpec(BaseModel):
    software: Literal['gromacs', 'gaussian'] = 'gromacs'
    batch_id: str = ''
    auto_resume: bool = True
    name: str = Field(min_length=1, max_length=160)
    description: str = Field('', max_length=4000)
    node_id: str = Field('', max_length=100)
    package_id: str | None = None
    steps: list[Step] = Field(min_length=1, max_length=16)
    resources: Resources = Field(default_factory=Resources)
    timeout_seconds: int = Field(3600, ge=5, le=604800)
    priority: int = Field(0, ge=0, le=10)

    @model_validator(mode='after')
    def validate_software(self):
        if self.software == 'gaussian':
            if any(s.subcommand != 'run' or len(s.arguments) != 1 or not s.arguments[0].lower().endswith(('.gjf', '.com')) for s in self.steps):
                raise ValueError('Gaussian requires run with one relative .gjf/.com input')
            if self.resources.gpu:
                raise ValueError('Gaussian GPU scheduling is not enabled')
        elif any(s.subcommand == 'run' for s in self.steps):
            raise ValueError('run is reserved for Gaussian')
        return self


def eligible(node: dict, resources: Resources, software='gromacs') -> bool:
    hw = node.get('hardware_info', {})
    sw = node.get('software_info', {}).get(software, {})
    from chemcompute.contribution import admits
    if hw.get('contribution') is not None and not admits(resources, hw['contribution']):
        return False
    return (node.get('status') == 'online' and sw.get('found', False)
            and hw.get('cpu_count_logical', 0) >= resources.cpu
            and hw.get('ram_available_mb', 0) >= resources.ram_mb
            and (not resources.gpu or (bool(hw.get('gpus')) and sw.get('cuda_enabled', False))))


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)
        self.root = self.path.parent / 'transfers'
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS packages_v2(id TEXT PRIMARY KEY, info TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks_v2(id TEXT PRIMARY KEY, spec TEXT NOT NULL,
              status TEXT NOT NULL, node TEXT NOT NULL, created REAL NOT NULL,
              updated REAL NOT NULL, progress INTEGER NOT NULL DEFAULT 0, log TEXT NOT NULL DEFAULT '',
              cancelled INTEGER NOT NULL DEFAULT 0, result_hash TEXT);
            ''')
        from chemcompute.recovery import RecoveryStore
        self.recovery = RecoveryStore(self.path)
        self.recovery.expire()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def create(self, spec):
        identity = 'task-' + uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            from chemcompute.update_maintenance import check_intake
            db.execute('BEGIN IMMEDIATE')
            check_intake(db)
            db.execute('INSERT INTO tasks_v2(id,spec,status,node,created,updated) VALUES (?,?,?,?,?,?)',
                       (identity, json.dumps(spec.model_dump()), 'queued', spec.node_id, now, now))
        return self.get(identity)

    def get(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks_v2 WHERE id=?', (identity,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value['spec'] = json.loads(value['spec'])
        return value

    def list(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM tasks_v2 ORDER BY created DESC LIMIT 500')]
        return [self.get(i) for i in ids]

    def claim(self, node):
        with self.connect() as db:
            from chemcompute.update_maintenance import check_intake
            db.execute('BEGIN IMMEDIATE')
            check_intake(db)
            active = db.execute("SELECT id FROM tasks_v2 WHERE node=? AND status IN ('running','recovering')", (node['node_id'],)).fetchone()
            if active:
                return None  # Never duplicate a task after worker disconnect.
            rows = db.execute("SELECT * FROM tasks_v2 WHERE status='queued' AND cancelled=0").fetchall()
            rows = sorted(rows, key=lambda r: (-json.loads(r['spec'])['priority'], r['created']))
            for row in rows:
                spec = TaskSpec(**json.loads(row['spec']))
                if (not row['node'] or row['node'] == node['node_id']) and eligible(node, spec.resources, spec.software):
                    lease = secrets.token_urlsafe(32)
                    db.execute("UPDATE tasks_v2 SET status='running',node=?,updated=?,lease_token=?,lease_until=?,attempt=attempt+1 WHERE id=?",
                               (node['node_id'], time.time(), lease, time.time()+90, row['id']))
                    return {'id': row['id'], 'spec': spec.model_dump(), 'lease_token': lease, 'attempt': row['attempt']+1}
        return None
