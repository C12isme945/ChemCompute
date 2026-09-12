"""
Dynamic Plugin Manager for ChemCompute Adapters.
Allows discovering, loading, and hot-reloading software adapters without restarting Agent.
"""

import hashlib
import importlib
import importlib.util
import inspect
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Dict, Optional, Type

from chemcompute.adapters.base import BaseAdapter
from chemcompute.adapters.gromacs import GromacsAdapter


class PluginManager:
    """Manages computation software adapters with hot-reloading capability."""

    def __init__(self, adapters_dir: Optional[Path] = None):
        self.adapters_dir = adapters_dir or (Path.cwd() / "adapters")
        self.adapters_dir.mkdir(parents=True, exist_ok=True)
        self._adapters: Dict[str, BaseAdapter] = {}

        # 1. Register default built-in adapters
        self.register_adapter("gromacs", GromacsAdapter())

        # 2. Discover external plugins in adapters_dir
        self.discover_external_plugins()

    def register_adapter(self, name: str, adapter_instance: BaseAdapter):
        self._adapters[name.lower()] = adapter_instance
        print(f"[PluginManager] Registered adapter '{name.lower()}'")

    def get_adapter(self, name: str) -> Optional[BaseAdapter]:
        return self._adapters.get(name.lower())

    def list_adapters(self) -> Dict[str, BaseAdapter]:
        return dict(self._adapters)

    def discover_external_plugins(self):
        """Scan adapters_dir for dynamic adapter modules."""
        if not self.adapters_dir.exists():
            return

        for item in self.adapters_dir.iterdir():
            if item.is_dir():
                # Check for adapter.py or __init__.py
                target_file = item / "adapter.py"
                if not target_file.exists():
                    target_file = item / "__init__.py"

                if target_file.exists():
                    self._load_adapter_from_file(item.name, target_file)

    def _load_adapter_from_file(self, name: str, file_path: Path) -> Optional[BaseAdapter]:
        try:
            module_name = f"chemcompute_adapter_{name}"
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find BaseAdapter subclass
            adapter_class: Optional[Type[BaseAdapter]] = None
            for _, obj in inspect.getmembers(module):
                if inspect.isclass(obj) and issubclass(obj, BaseAdapter) and obj is not BaseAdapter:
                    adapter_class = obj
                    break

            if adapter_class:
                instance = adapter_class()
                self._adapters[name.lower()] = instance
                print(f"[PluginManager] Loaded external adapter: {name.lower()} from {file_path}")
                return instance
            else:
                print(f"[PluginManager] No BaseAdapter subclass found in {file_path}")
        except Exception as e:
            print(f"[PluginManager] Error loading adapter {name}: {e}")
        return None

    def hot_reload_adapter(
        self,
        name: str,
        zip_path: Path,
        expected_sha256: Optional[str] = None
    ) -> bool:
        """
        Unpack zip package, verify integrity, dynamically reload module, and update registry.
        Does not require restarting the agent daemon!
        """
        if not zip_path.exists():
            print(f"[PluginManager] Hot-reload failed: {zip_path} does not exist.")
            return False

        # Verify SHA256 if provided
        if expected_sha256:
            h = hashlib.sha256()
            with open(zip_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            computed = h.hexdigest()
            if computed.lower() != expected_sha256.lower():
                print(f"[PluginManager] SHA256 mismatch: expected {expected_sha256}, got {computed}")
                return False

        target_dir = self.adapters_dir / name.lower()
        staging_dir = self.adapters_dir / f"{name.lower()}_staging"

        try:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            staging_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(staging_dir)

            # Locate adapter entrypoint in staging
            entry = staging_dir / "adapter.py"
            if not entry.exists():
                entry = staging_dir / "__init__.py"
            if not entry.exists():
                # Check single child dir
                children = [c for c in staging_dir.iterdir() if c.is_dir()]
                if children and (children[0] / "adapter.py").exists():
                    entry = children[0] / "adapter.py"

            if not entry.exists():
                print(f"[PluginManager] Hot-reload error: No adapter.py found in package.")
                shutil.rmtree(staging_dir, ignore_errors=True)
                return False

            # Replace directory
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            staging_dir.rename(target_dir)

            # Re-locate in target_dir
            final_entry = target_dir / entry.name
            if not final_entry.exists():
                final_entry = target_dir / "adapter.py"
                if not final_entry.exists():
                    final_entry = target_dir / "__init__.py"

            instance = self._load_adapter_from_file(name, final_entry)
            if instance:
                print(f"[PluginManager] Successfully hot-reloaded adapter '{name}'!")
                return True
            return False
        except Exception as e:
            print(f"[PluginManager] Exception during hot-reload of '{name}': {e}")
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            return False
