import os
import sys
import threading

import pytest

from chemcompute.node.process import run_capped
from chemcompute.secrets_store import deepseek_key, read_credentials, save_credentials


def test_cancel_native_process_before_next_step():
    event = threading.Event()
    timer = threading.Timer(.3, event.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            run_capped([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=10, cancel_event=event)
    finally:
        timer.cancel()


@pytest.mark.skipif(os.name != 'nt', reason='Windows current-user DPAPI')
def test_dpapi_persistence_has_no_plaintext(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = {'deepseek_key': 'unit-test-only-credential', 'model': 'test-model'}
    save_credentials(values)
    assert read_credentials() == values
    assert b'unit-test-only-credential' not in (tmp_path / 'config/desktop-credentials.dpapi').read_bytes()
    save_credentials({**values, 'model': 'updated-model'})
    assert read_credentials()['model'] == 'updated-model'


def test_environment_key_does_not_require_windows(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key-from-environment')
    assert deepseek_key() == 'test-key-from-environment'
