"""Local process lifecycle and authenticated data for the desktop console."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

from chemcompute.common.config import ControllerConfig, NodeConfig, load_yaml_config

PID_FILE = Path('data/desktop-process.json')


def role() -> str:
    try:
        value = json.loads(Path('role.json').read_text('utf-8'))['role']
        if value in {'controller', 'node', 'both'}:
            return value
    except (OSError, ValueError, KeyError):
        pass
    raise RuntimeError('尚未配置本机角色，请先运行安装程序。')


def controller_url() -> str:
    if role() == 'node':
        return load_yaml_config('config/node.yaml', NodeConfig).controller_url.rstrip('/')
    config = load_yaml_config('config/controller.yaml', ControllerConfig)
    host = config.host
    if host in {'0.0.0.0', '*'}:
        host = '127.0.0.1'
    elif host == '::':
        host = '::1'
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    return f'http://{host}:{config.port}'


def admin_key() -> str | None:
    if role() == 'node':
        return None
    config = load_yaml_config('config/controller.yaml', ControllerConfig)
    if config.admin_secret:
        return config.admin_secret.strip()
    if os.environ.get('CHEMCOMPUTE_ADMIN_SECRET'):
        return os.environ['CHEMCOMPUTE_ADMIN_SECRET'].strip()
    try:
        return Path(config.secret_file).read_text('utf-8').strip()
    except OSError:
        return None


def process_matches(process, record: dict) -> bool:
    """Fail closed: exact PID, start time, executable, argv, and runtime path."""
    try:
        return (
            process.pid == record['pid']
            and abs(process.create_time() - record['created']) < 0.01
            and os.path.normcase(process.exe()) == os.path.normcase(record['exe'])
            and process.cmdline() == record['argv']
            and 'desktop-run' in record['argv']
            and record['home'] == str(Path.cwd().resolve())
        )
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def record_process() -> None:
    process = psutil.Process()
    record = {'pid': process.pid, 'created': process.create_time(), 'exe': process.exe(),
              'argv': process.cmdline(), 'home': str(Path.cwd().resolve())}
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PID_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(PID_FILE)


def running_process():
    try:
        record = json.loads(PID_FILE.read_text('utf-8'))
        process = psutil.Process(record['pid'])
        return process if process_matches(process, record) and process.is_running() else None
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return None


def clear_own_record() -> None:
    try:
        record = json.loads(PID_FILE.read_text('utf-8'))
        if record['pid'] == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError):
        pass


def start() -> str:
    existing = running_process()
    if existing:
        return f'后台已运行，PID {existing.pid}'
    role()  # Refuse an unconfigured instance.
    Path('logs').mkdir(exist_ok=True)
    if getattr(sys, 'frozen', False):
        command = [sys.executable, 'desktop-run']
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / 'launcher.py'), 'desktop-run']
    environment = dict(os.environ, CHEMCOMPUTE_HOME=str(Path.cwd().resolve()))
    with Path('logs/launcher.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(60):
        process = running_process()
        if process:
            return f'后台已启动，PID {process.pid}'
        if child.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError('后台未确认启动。请查看日志；升级前运行的旧版需先手动退出。')


def stop() -> str:
    process = running_process()
    if process is None:
        return '未找到可确认身份的本机后台。'
    # End only the validated instance. Computation should be stopped by the user first.
    process.terminate()
    try:
        process.wait(timeout=8)
    except psutil.TimeoutExpired as exc:
        raise RuntimeError('后台尚未退出，请检查正在执行的任务。') from exc
    PID_FILE.unlink(missing_ok=True)
    return '本机后台已停止。'


def restart() -> str:
    stop()
    return start()


def snapshot() -> dict:
    current_role = role()
    url = controller_url()
    process = running_process()
    result = {'role': current_role, 'url': url, 'running': process is not None,
              'pid': process.pid if process else None, 'online': False, 'nodes': [], 'notice': ''}
    try:
        with httpx.Client(timeout=2, follow_redirects=False) as client:
            response = client.get(url + '/')
            result['online'] = response.status_code == 200
            key = admin_key()
            if key and result['online']:
                response = client.get(url + '/api/v1/nodes', headers={'Authorization': 'Bearer ' + key})
                response.raise_for_status()
                result['nodes'] = response.json()
            elif current_role == 'node':
                result['notice'] = '等待自动注册。连接成功后，任务将由主控自动安排。'
    except (httpx.HTTPError, ValueError):
        result['notice'] = '主控端尚未就绪或认证失败，请检查地址与日志。'
    if current_role == 'node':
        try:
            status = json.loads(Path('config/connection-status.json').read_text('utf-8'))
            fresh = process is not None and time.time() - status['time'] < 90
            messages = {'connected': '已加入并连接主控。任务自动领取，可关闭此窗口；请保持登录、联网且不休眠。',
                        'registered': '已成功加入，等待首次心跳确认。',
                        'needs_invite': '需要有效邀请码，请让主控重新导出专属部署包或在节点页更新配置。',
                        'registration_failed': '注册未成功：请检查邀请码是否过期或已使用。',
                        'waiting': '暂时无法连接，后台会自动重试。'}
            result['notice'] = messages.get(status['state'], '等待连接') if fresh else '后台未连接或心跳已过期；运行中的后台会自动重试。'
            result['online'] = fresh and status['state'] == 'connected'
        except (OSError, ValueError, KeyError, TypeError):
            result['online'] = False
    return result
