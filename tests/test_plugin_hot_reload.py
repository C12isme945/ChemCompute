"""
Tests for PluginManager dynamic adapter hot-reloading without process restarts.
"""

import hashlib
import tempfile
import zipfile
from pathlib import Path
import pytest

from chemcompute.agent.plugin_manager import PluginManager


MOCK_ADAPTER_CODE = """
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from chemcompute.adapters.base import BaseAdapter

class MockOrcaAdapter(BaseAdapter):
    def __init__(self):
        super().__init__("orca")
        self.version = "5.0.4"

    def detect(self) -> Dict[str, Any]:
        return {"available": True, "version": self.version}

    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        return True

    def run(self, workspace_dir: Path, parameters: Dict[str, Any], progress_callback: Optional[Callable[[float, str], None]] = None) -> bool:
        if progress_callback:
            progress_callback(100.0, "ORCA calculation finished successfully.")
        return True

    def collect(self, workspace_dir: Path) -> List[Path]:
        return []
"""


def test_plugin_hot_reloading(tmp_path):
    adapters_dir = tmp_path / "adapters"
    adapters_dir.mkdir()

    pm = PluginManager(adapters_dir=adapters_dir)

    # Initially, 'orca' adapter is not installed
    assert pm.get_adapter("orca") is None
    assert pm.get_adapter("gromacs") is not None

    # Build a mock orca-adapter-1.0.0.zip
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "adapter.py"
        src.write_text(MOCK_ADAPTER_CODE, encoding="utf-8")

        zip_path = tmp_path / "orca-adapter-1.0.0.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(src, "adapter.py")

        # Compute sha256
        h = hashlib.sha256()
        with open(zip_path, "rb") as f:
            h.update(f.read())
        sha = h.hexdigest()

        # Hot-reload into PluginManager
        success = pm.hot_reload_adapter("orca", zip_path, expected_sha256=sha)
        assert success is True

    # Now verify 'orca' adapter is active!
    orca = pm.get_adapter("orca")
    assert orca is not None
    info = orca.detect()
    assert info["available"] is True
    assert info["version"] == "5.0.4"
    print("\n[OK] Adapter hot-reload verified successfully!")
