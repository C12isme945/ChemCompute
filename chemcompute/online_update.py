"""Trusted GitHub releases, verified downloads and idle-only Windows upgrades."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from chemcompute import __version__
from chemcompute import desktop_backend as backend
from chemcompute.common.security import ensure_secure_directory, secure_path
from chemcompute.update_maintenance import finish_install, prepare_install

REPO = 'C12isme945/ChemCompute'
API = f'https://api.github.com/repos/{REPO}/releases?per_page=100'
MAX_SIZE = 256 * 1024 * 1024
SEMVER = re.compile(r'^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')


def version_key(value):
    match = SEMVER.fullmatch(value)
    if not match:
        raise ValueError('版本号格式无效')
    major, minor, patch, pre = match.groups()
    parts = []
    if pre:
        for item in pre.split('.'):
            if not item or (item.isdigit() and len(item) > 1 and item.startswith('0')):
                raise ValueError('预发布版本格式无效')
            parts.append((0, int(item)) if item.isdigit() else (1, item))
    return int(major), int(minor), int(patch), pre is None, tuple(parts)


def trusted_url(url, *, redirected=False):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('更新地址必须使用官方 HTTPS 来源')
    if parsed.hostname == 'github.com' and parsed.path.startswith(f'/{REPO}/releases/download/'):
        return url
    if redirected and parsed.hostname in {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
        return url
    raise ValueError('拒绝非官方更新地址')


def select_release(releases, current_version=__version__, include_prerelease=True):
    current = version_key(current_version)
    candidates = []
    for release in releases:
        if release.get('draft') or (release.get('prerelease') and not include_prerelease):
            continue
        try:
            key = version_key(release.get('tag_name', ''))
        except (ValueError, TypeError):
            continue
        if not include_prerelease and not key[3]:
            continue
        if key <= current:
            continue
        assets = [a for a in release.get('assets', []) if a.get('name') == 'ChemCompute-Setup.exe' and a.get('state', 'uploaded') == 'uploaded']
        if len(assets) != 1:
            continue
        asset = assets[0]
        digest = asset.get('digest') or ''
        if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
            continue  # Never launch assets with missing trusted digest.
        try:
            trusted_url(asset['browser_download_url'])
            size = int(asset['size'])
            if not 0 < size <= MAX_SIZE:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        candidates.append((key, {'version': release['tag_name'].lstrip('v'), 'url': asset['browser_download_url'],
                                'sha256': digest[7:].lower(), 'size': size,
                                'notes': (release.get('body') or '')[:60000]}))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


@retry(retry=retry_if_exception_type(httpx.TransportError), stop=stop_after_attempt(3),
       wait=wait_random_exponential(multiplier=1, max=4), reraise=True)
def check_updates(current_version=__version__, include_prerelease=True):
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': f'ChemCompute/{__version__}'})
        response.raise_for_status()
        if len(response.content) > 4 * 1024 * 1024:
            raise ValueError('发布信息过大')
        releases = response.json()
        if not isinstance(releases, list):
            raise ValueError('发布信息格式无效')
        return select_release(releases, current_version, include_prerelease)


def verified_file(path, release):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != release['size']:
        return False
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest() == release['sha256']


@retry(retry=retry_if_exception_type(httpx.TransportError), stop=stop_after_attempt(3),
       wait=wait_random_exponential(multiplier=1, max=4), reraise=True)
def download_update(release, target, progress=lambda done, total: None, cancel=lambda: False):
    if cancel():
        raise InterruptedError('下载已取消')
    target = Path(target)
    url = trusted_url(release['url'])
    if not re.fullmatch('[0-9a-f]{64}', release['sha256']) or not 0 < release['size'] <= MAX_SIZE:
        raise ValueError('缺少有效校验信息')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.partial-' + uuid.uuid4().hex)
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False) as client:
            for _ in range(6):
                with client.stream('GET', url) as response:
                    if response.is_redirect:
                        url = trusted_url(str(response.url.join(response.headers['location'])), redirected=True)
                        continue
                    response.raise_for_status()
                    done = 0
                    with temporary.open('wb') as stream:
                        for chunk in response.iter_bytes(65536):
                            if cancel():
                                raise InterruptedError('下载已取消')
                            if time.monotonic() - started > 600:
                                raise TimeoutError('下载超过十分钟，请稍后重试')
                            done += len(chunk)
                            if done > release['size'] or done > MAX_SIZE:
                                raise ValueError('下载大小超出发布信息')
                            stream.write(chunk)
                            progress(done, release['size'])
                    if not verified_file(temporary, release):
                        raise ValueError('安装包校验失败，未保存或执行')
                    temporary.replace(target)
                    return target
            raise ValueError('下载重定向次数过多')
    finally:
        temporary.unlink(missing_ok=True)


def process_record(process):
    return {'pid': process.pid, 'created': process.create_time(), 'exe': process.exe(),
            'argv': process.cmdline()}


def launch_update(release, installer):
    """Returns after independent helper starts. GUI must then close normally."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise RuntimeError('请使用已安装的 Windows 桌面程序执行更新')
    if version_key(release['version']) <= version_key(__version__):
        raise ValueError('不能安装相同或更早版本')
    # Refresh authoritative metadata immediately before execution.
    fresh = check_updates()
    if fresh != release or not verified_file(installer, fresh):
        raise ValueError('发布信息发生变化或校验失败，请重新检查并下载')
    ticket = prepare_install(ttl_seconds=3600)
    try:
        home = Path.cwd().resolve()
        executable = Path(sys.executable).resolve()
        worker = backend.running_process()
        updates = ensure_secure_directory(home / 'updates')
        folder = ensure_secure_directory(updates / ('install-' + uuid.uuid4().hex))
        helper = folder / 'install.ps1'
        shutil.copyfile(Path(__file__).with_name('install_update.ps1'), helper)
        request = {'home': str(home), 'executable': str(executable), 'installer': str(Path(installer).resolve()),
                   'sha256': fresh['sha256'], 'version': fresh['version'], 'ticket': ticket,
                   'gui': process_record(psutil.Process()), 'backend': process_record(worker) if worker else None}
        path = folder / 'request.json'
        path.write_text(json.dumps(request), encoding='utf-8')
        if not secure_path(path):
            raise RuntimeError('无法保护更新请求文件')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        subprocess.Popen([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-RequestPath', str(path)],
                         cwd=home, creationflags=subprocess.CREATE_NO_WINDOW)
        return '更新助手已启动，正在退出桌面窗口。'
    except Exception:
        finish_install(ticket)
        raise


def complete_update(request_path):
    request = json.loads(Path(request_path).read_text('utf-8'))
    if Path(request['home']).resolve() != Path.cwd().resolve():
        raise ValueError('更新运行目录不匹配')
    return finish_install(request['ticket'])
