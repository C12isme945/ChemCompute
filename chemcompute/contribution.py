"""Local resource offers, separate from physical hardware telemetry."""
from __future__ import annotations

import math
import os
import re
import uuid
from pathlib import Path

import psutil
from pydantic import BaseModel, Field

from chemcompute.common.security import ensure_secure_directory, secure_path


class ContributionSettings(BaseModel):
    cpu_percent: int = Field(50, ge=1, le=100)
    memory_percent: int = Field(50, ge=1, le=90)
    gpu_enabled: bool = False
    paused: bool = False


def settings_path(config_path=None):
    return Path(config_path or 'config/node.yaml').with_name('contribution.json')


def load_settings(config_path=None):
    path = settings_path(config_path)
    try:
        return ContributionSettings.model_validate_json(path.read_text('utf-8'))
    except FileNotFoundError:
        return ContributionSettings()
    except (OSError, ValueError):
        # A damaged preferences file must never silently resume contribution.
        return ContributionSettings(paused=True)


def save_settings(config_path, settings):
    settings = ContributionSettings.model_validate(settings)
    path = settings_path(config_path)
    ensure_secure_directory(path.parent)
    temporary = path.with_name(f'.contribution-{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(settings.model_dump_json(indent=2), encoding='utf-8')
        if not secure_path(temporary):
            raise OSError('Cannot protect contribution settings')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def compute_budget(settings, hardware):
    hw = hardware if isinstance(hardware, dict) else hardware.model_dump()
    cores = max(1, int(hw.get('cpu_count_logical') or 1))
    return {'cpu_cores': max(1, min(cores, math.ceil(cores * settings.cpu_percent / 100))),
            'ram_mb': max(0, int(hw.get('ram_total_mb', 0) * settings.memory_percent / 100)),
            'gpu_enabled': settings.gpu_enabled, 'paused': settings.paused,
            'cpu_percent': settings.cpu_percent, 'memory_percent': settings.memory_percent}


def snapshot(config_path=None):
    return compute_budget(load_settings(config_path), {'cpu_count_logical': psutil.cpu_count() or 1,
                          'ram_total_mb': psutil.virtual_memory().total // 1024**2})


def admits(resources, budget):
    try:
        return (not budget.get('paused', False) and resources.cpu <= budget['cpu_cores']
                and resources.ram_mb <= budget['ram_mb']
                and (not resources.gpu or budget.get('gpu_enabled', False)))
    except (KeyError, TypeError, AttributeError):
        return False


def pin_process(proc, cores):
    """Restrict a native computation and existing descendants to offered cores.

    Affinity is a core allocation, not a CPU utilization percentage limiter.
    A process that cannot be constrained must fail rather than run unrestricted.
    """
    allowed = psutil.Process().cpu_affinity()[:max(1, int(cores))]
    if not allowed:
        raise RuntimeError('No permitted CPU cores')
    try:
        process = psutil.Process(proc.pid)
        for child in [process, *process.children(recursive=True)]:
            try:
                child.cpu_affinity(allowed)
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        # Windows can deny process inspection during teardown. Only tolerate it
        # after observing termination; a live unconstrained process still fails.
        if proc.poll() is None:
            raise


def execution_env(budget):
    env = os.environ.copy()
    env.update(OMP_NUM_THREADS=str(budget['cpu_cores']), OMP_THREAD_LIMIT=str(budget['cpu_cores']))
    if not budget['gpu_enabled']:
        env.update(CUDA_VISIBLE_DEVICES='-1', ROCR_VISIBLE_DEVICES='-1', HIP_VISIBLE_DEVICES='-1')
    return env


def gromacs_arguments(subcommand, arguments, budget):
    args = list(arguments)
    if subcommand != 'mdrun':
        return args
    if '-pin' in args:
        for i, arg in enumerate(args):
            if arg == '-pin' and (i+1 >= len(args) or args[i+1] != 'off'):
                raise ValueError('Use -pin off so the contribution CPU placement is preserved')
    else:
        args += ['-pin', 'off']
    for flag in ('-nt', '-ntomp', '-ntmpi', '-ntomp_pme'):
        for i, arg in enumerate(args):
            if arg == flag:
                if i+1 >= len(args) or not args[i+1].isdigit() or not 1 <= int(args[i+1]) <= budget['cpu_cores']:
                    raise ValueError(f'{flag} exceeds CPU contribution; specify 1..{budget["cpu_cores"]}')
    if not any(flag in args for flag in ('-nt', '-ntomp', '-ntmpi')):
        args += ['-nt', str(budget['cpu_cores'])]
    if not budget['gpu_enabled']:
        for flag in ('-nb', '-pme', '-bonded', '-update'):
            values = [args[i+1] if i+1 < len(args) else '' for i, arg in enumerate(args) if arg == flag]
            if any(value != 'cpu' for value in values):
                raise ValueError(f'GPU contribution disabled: use {flag} cpu')
            if not values:
                args += [flag, 'cpu']
    return args


def validate_gaussian_input(path, budget):
    text = Path(path).read_text('utf-8', errors='replace')
    for line in text.splitlines():
        if not line.lstrip().startswith('%'):
            continue
        key, _, value = line.strip().partition('=')
        key = key.lower()
        if key in {'%cpu', '%lindaworkers', '%nproclinda'}:
            raise ValueError('Explicit CPU placement / Linda is outside local contribution control')
        if key.startswith('%gpu') and not budget['gpu_enabled']:
            raise ValueError('GPU contribution disabled')
        if key in {'%nproc', '%nprocshared'} and (not value.strip().isdigit() or not 1 <= int(value) <= budget['cpu_cores']):
            raise ValueError('Gaussian processor request exceeds CPU contribution')
        if key == '%mem':
            match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB|KW|MW|GW|TW|B|W)?\s*', value, re.I)
            if not match:
                raise ValueError('Cannot validate Gaussian memory request')
            unit = (match[2] or 'W').upper()
            multiplier = {'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}.get(unit[0], 1)
            requested = float(match[1]) * multiplier * (8 if unit.endswith('W') else 1)
            if requested > budget['ram_mb'] * 1024**2:
                raise ValueError('Gaussian memory request exceeds contribution budget')
