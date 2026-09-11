import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from chemcompute import desktop_backend as desktop
from chemcompute.common.config import ControllerConfig, NodeConfig, save_yaml_config


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'role.json').write_text('{"role":"both"}')
    return tmp_path


def test_process_identity_requires_all_fields(home):
    process = Mock(pid=42)
    process.create_time.return_value = 1000.0
    process.exe.return_value = str(home / 'ChemCompute.exe')
    process.cmdline.return_value = [process.exe(), 'desktop-run']
    record = {'pid': 42, 'created': 1000.0, 'exe': process.exe(), 'argv': process.cmdline(), 'home': str(home)}
    assert desktop.process_matches(process, record)
    for field, value in [('pid', 99), ('created', 1001.0), ('exe', str(home / 'other/ChemCompute.exe')),
                         ('argv', [process.exe(), 'console']), ('home', str(home.parent))]:
        changed = dict(record, **{field: value})
        assert not desktop.process_matches(process, changed)
    assert not desktop.process_matches(process, {'pid': 42})


def test_node_only_cannot_read_admin_secret(home):
    Path('role.json').write_text('{"role":"node"}')
    Path('data').mkdir()
    Path('data/chemcompute-admin.secret').write_text('fixture-secret')
    save_yaml_config(NodeConfig(controller_url='https://compute.example:443'), 'config/node.yaml')
    assert desktop.admin_key() is None
    assert desktop.controller_url() == 'https://compute.example:443'


@pytest.mark.parametrize('host,expected', [('0.0.0.0', '127.0.0.1'), ('::', '[::1]'), ('100.64.0.10', '100.64.0.10')])
def test_url_respects_config(home, host, expected):
    save_yaml_config(ControllerConfig(host=host, port=8888), 'config/controller.yaml')
    assert desktop.controller_url() == f'http://{expected}:8888'


def test_environment_secret_precedence(home, monkeypatch):
    save_yaml_config(ControllerConfig(), 'config/controller.yaml')
    Path('data').mkdir()
    Path('data/chemcompute-admin.secret').write_text('old-fixture')
    monkeypatch.setenv('CHEMCOMPUTE_ADMIN_SECRET', 'environment-fixture')
    assert desktop.admin_key() == 'environment-fixture'


def test_start_does_not_spawn_if_already_running(home, monkeypatch):
    monkeypatch.setattr(desktop, 'running_process', lambda: Mock(pid=42))
    launch = Mock()
    monkeypatch.setattr(desktop.subprocess, 'Popen', launch)
    assert '42' in desktop.start()
    launch.assert_not_called()


def test_invalid_pid_record_never_stops_unrelated_process(home, monkeypatch):
    Path('data').mkdir()
    Path('data/desktop-process.json').write_text(json.dumps({'pid': 42}))
    process = Mock(pid=42)
    monkeypatch.setattr(desktop.psutil, 'Process', lambda pid: process)
    desktop.stop()
    process.terminate.assert_not_called()
