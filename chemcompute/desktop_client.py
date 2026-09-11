"""Desktop API facade; secrets never travel in URLs or uploaded packages."""
from __future__ import annotations

from pathlib import Path

import httpx

from chemcompute import desktop_backend as backend
from chemcompute.packages import MAX_ARCHIVE_BYTES, sha256_file
from chemcompute.secrets_store import read_credentials


def connection():
    credentials = read_credentials()
    override = credentials.get('controller_url', '').strip()
    return (override or backend.controller_url()).rstrip('/'), (credentials.get('admin_key') if override else backend.admin_key())


def call(method, path, **kwargs):
    url, key = connection()
    if not key:
        raise RuntimeError('需要主控管理员密钥。请在设置中配置远程连接，或先启动本机主控。')
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        response = client.request(method, url + path, headers={'Authorization': 'Bearer ' + key}, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get('detail', 'Request failed')
        except ValueError:
            detail = 'Request failed'
        raise RuntimeError(f'HTTP {response.status_code}: {detail}')
    return response.json()


def upload(path):
    with Path(path).open('rb') as stream:
        return call('POST', '/api/v2/packages', content=stream)


def download_result(identity, destination):
    url, key = connection()
    if not key:
        raise RuntimeError('Administrator authentication required')
    target = Path(destination)
    temporary = target.with_suffix(target.suffix + '.partial')
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as client, client.stream('GET', url + '/api/v2/tasks/' + identity + '/results', headers={'Authorization': 'Bearer ' + key}) as response:
            if response.status_code != 200:
                raise RuntimeError(f'Result unavailable (HTTP {response.status_code})')
            count = 0
            with temporary.open('wb') as output:
                for chunk in response.iter_bytes(65536):
                    count += len(chunk)
                    if count > MAX_ARCHIVE_BYTES:
                        raise RuntimeError('Result exceeds size limit')
                    output.write(chunk)
            if sha256_file(temporary) != response.headers.get('X-SHA256'):
                raise RuntimeError('Result checksum mismatch')
        temporary.replace(target)
        return str(target)
    finally:
        temporary.unlink(missing_ok=True)
