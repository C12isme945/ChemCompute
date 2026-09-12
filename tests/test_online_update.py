import hashlib
from copy import deepcopy

import httpx
import pytest

from chemcompute import online_update as update


def release(tag='v0.5.0', raw=b'verified installer', **changes):
    data = {'tag_name': tag, 'draft': False, 'prerelease': True, 'body': 'Update', 'assets': [
        {'name': 'ChemCompute-Setup.exe', 'size': len(raw),
         'browser_download_url': f'https://github.com/{update.REPO}/releases/download/{tag}/ChemCompute-Setup.exe',
         'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()}]}
    data.update(changes)
    return data


def test_semver_order_and_release_selection():
    values = ['0.5.0-alpha', '0.5.0-alpha.1', '0.5.0-alpha.beta', '0.5.0-beta', '0.5.0-beta.2', '0.5.0-beta.11', '0.5.0-rc.1', '0.5.0']
    assert sorted(reversed(values), key=update.version_key) == values
    assert update.select_release([release('v0.5.0'), release('v0.6.0')], '0.4.0')['version'] == '0.6.0'
    assert update.select_release([release('v0.4.0'), release('v0.3.0')], '0.4.0') is None
    assert update.select_release([release()], '0.4.0', False) is None
    assert update.select_release([release(draft=True)], '0.4.0') is None
    assert update.select_release([release(prerelease=False)], '0.4.0', False)


@pytest.mark.parametrize('bad', ['v1', '1.2', '1.2.3-01', '1.2.3-a..b', '01.2.3', '1.2.3;evil'])
def test_bad_versions(bad):
    with pytest.raises(ValueError):
        update.version_key(bad)


@pytest.mark.parametrize('url', ['http://github.com/x', 'https://evil.example/x',
                               'https://github.com/another/repo/releases/download/v1/setup.exe',
                               'https://github.com@evil.example/x', 'https://github.com:444/x'])
def test_untrusted_urls(url):
    with pytest.raises(ValueError):
        update.trusted_url(url, redirected=True)


def test_no_hash_no_executable():
    data = release()
    for asset in [dict(data['assets'][0], digest=None), dict(data['assets'][0], size=update.MAX_SIZE+1),
                  dict(data['assets'][0], name='other.exe')]:
        changed = deepcopy(data)
        changed['assets'] = [asset]
        assert update.select_release([changed], '0.4.0') is None


def fake_http(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(update.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


def test_download_redirect_integrity_and_progress(tmp_path, monkeypatch):
    raw = b'authentic bytes'
    data = update.select_release([release(raw=raw)], '0.4.0')
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.host == 'github.com':
            return httpx.Response(302, headers={'Location': 'https://release-assets.githubusercontent.com/asset'})
        return httpx.Response(200, content=raw)
    fake_http(monkeypatch, handler)
    progress = []
    target = update.download_update(data, tmp_path/'setup.exe', lambda a,b: progress.append((a,b)))
    assert target.read_bytes() == raw
    assert progress[-1] == (len(raw), len(raw))
    assert len(calls) == 2


@pytest.mark.parametrize('mode', ['tampered', 'oversize', 'cancelled', 'evil_redirect'])
def test_failed_download_never_replaces_existing(tmp_path, monkeypatch, mode):
    raw = b'expected'
    data = update.select_release([release(raw=raw)], '0.4.0')
    def handler(request):
        if mode == 'evil_redirect':
            return httpx.Response(302, headers={'Location': 'https://evil.example/setup.exe'})
        return httpx.Response(200, content=b'tampered' if mode == 'tampered' else raw*2 if mode == 'oversize' else raw)
    fake_http(monkeypatch, handler)
    target = tmp_path/'setup.exe'
    target.write_bytes(b'prior')
    with pytest.raises((ValueError, InterruptedError)):
        update.download_update(data, target, cancel=lambda: mode == 'cancelled')
    assert target.read_bytes() == b'prior'
    assert not list(tmp_path.glob('*.partial-*'))


def test_source_build_cannot_launch_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(update.sys, 'frozen', False, raising=False)
    with pytest.raises(RuntimeError):
        update.launch_update({}, tmp_path/'setup.exe')
