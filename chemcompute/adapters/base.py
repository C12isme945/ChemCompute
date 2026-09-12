"""
Abstract Base Class for ChemCompute Runtime Adapters.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional


class BaseAdapter(ABC):
    """
    Standard interface for chemistry computation software adapters.
    Each software (GROMACS, ORCA, LAMMPS, COMSOL, etc.) implements this lifecycle:
    1. detect(): checks if software is installed and detects version / capabilities.
    2. prepare(): unpacks inputs, builds runtime input (e.g. grompp -> tpr).
    3. run(): executes computation with streaming stdout/stderr progress updates.
    4. collect(): gathers result files and packages artifacts.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def detect(self) -> Dict[str, Any]:
        """Detect software availability, version, and features on this host."""
        pass

    @abstractmethod
    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        """Pre-process job inputs before running main calculation."""
        pass

    @abstractmethod
    def run(
        self,
        workspace_dir: Path,
        parameters: Dict[str, Any],
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """
        Execute computation.
        progress_callback(pct: float, log_line: str) may be called periodically.
        Returns True if successful, False otherwise.
        """
        pass

    @abstractmethod
    def collect(self, workspace_dir: Path) -> List[Path]:
        """Collect all result and output files to return to the controller."""
        pass
