import configparser
import hashlib
import zipfile

import pytest

from chemcompute.entry import onboard
from chemcompute.invite_bundle import validate_url, write_bundle


@pytest.mark.parametrize('url', ['http://example.com', 'https://u:p@example.com', 'https://example.com/?token=x', 'https://example.com/#x', 'file:///tmp'])
def test_reject_unsafe_invitation_url(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_bundle_contains_only_short_invite_and_verified_installer(tmp_path):
    installer = tmp_path/'setup.exe'
    installer.write_bytes(b'installer fixture')
    release = {'size': installer.stat().st_size, 'sha256': hashlib.sha256(installer.read_bytes()).hexdigest()}
    invite = {'invite_code': 'cc-inv-'+'a'*32, 'expires_at': '2027-01-01T00:00:00Z'}
    target = tmp_path/'join.zip'
    write_bundle(target, installer, release, 'https://controller.example', invite)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {'ChemCompute-Setup.exe', 'chemcompute-join.ini', '请先阅读.txt'}
        parser = configparser.ConfigParser()
        parser.read_string(archive.read('chemcompute-join.ini').decode('utf-16'))
        assert parser['join']['invite'] == invite['invite_code']
        assert 'admin' not in parser['join']
    installer.write_bytes(b'tampered fixture!')
    with pytest.raises(ValueError):
        write_bundle(target, installer, release, 'https://controller.example', invite)


def test_invited_onboarding_preserves_existing_enrollment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'role.json').write_text('{"role":"controller"}')
    ini = tmp_path/'onboard.ini'
    ini.write_text('[setup]\nrole=node\ninvited=1\nurl=https://example.com\ninvite=cc-inv-'+'a'*32)
    with pytest.raises(ValueError, match='Existing enrollment'):
        onboard(str(ini))
    assert (tmp_path/'role.json').read_text() == '{"role":"controller"}'
