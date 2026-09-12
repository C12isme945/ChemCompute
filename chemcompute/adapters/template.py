"""
Stub adapters for future chemical and multiphysics simulation engines.
"""

from pathlib import Path
from typing import Dict, Any, List, Callable, Optional
from chemcompute.adapters.base import BaseAdapter


class OrcaAdapter(BaseAdapter):
    """ORCA Quantum Chemistry Adapter (Stub)."""
    def __init__(self):
        super().__init__(name="orca")

    def detect(self) -> Dict[str, Any]:
        return {"available": False, "version": "Not configured", "path": ""}

    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        return True

    def run(self, workspace_dir: Path, parameters: Dict[str, Any], progress_callback: Optional[Callable[[float, str], None]] = None) -> bool:
        if progress_callback:
            progress_callback(0.0, "ORCA adapter is scheduled for Post-MVP.")
        return False

    def collect(self, workspace_dir: Path) -> List[Path]:
        return []


class ComsolAdapter(BaseAdapter):
    """COMSOL Multiphysics Adapter (Stub)."""
    def __init__(self):
        super().__init__(name="comsol")

    def detect(self) -> Dict[str, Any]:
        return {"available": False, "version": "Not configured", "path": ""}

    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        return True

    def run(self, workspace_dir: Path, parameters: Dict[str, Any], progress_callback: Optional[Callable[[float, str], None]] = None) -> bool:
        if progress_callback:
            progress_callback(0.0, "COMSOL adapter is scheduled for Post-MVP.")
        return False

    def collect(self, workspace_dir: Path) -> List[Path]:
        return []


class LammpsAdapter(BaseAdapter):
    """LAMMPS Molecular Dynamics Adapter (Stub)."""
    def __init__(self):
        super().__init__(name="lammps")

    def detect(self) -> Dict[str, Any]:
        return {"available": False, "version": "Not configured", "path": ""}

    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        return True

    def run(self, workspace_dir: Path, parameters: Dict[str, Any], progress_callback: Optional[Callable[[float, str], None]] = None) -> bool:
        if progress_callback:
            progress_callback(0.0, "LAMMPS adapter is scheduled for Post-MVP.")
        return False

    def collect(self, workspace_dir: Path) -> List[Path]:
        return []
