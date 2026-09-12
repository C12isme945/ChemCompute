"""
Software detection scanner for ChemCompute Node.
Scans for GROMACS, ORCA, COMSOL, and scientific Python packages.
"""

import os
import shutil
import sys
from pathlib import Path

from chemcompute.adapters.gromacs import GromacsAdapter
from chemcompute.common.models import (
    SoftwareCatalog,
    SoftwareGromacs,
    SoftwareOrca,
    SoftwareComsol,
)


def scan_gromacs() -> SoftwareGromacs:
    """Scan and verify GROMACS presence."""
    adapter = GromacsAdapter()
    info = adapter.detect()
    return SoftwareGromacs(
        available=info.get("available", False),
        version=info.get("version", "Unknown"),
        is_wsl=info.get("is_wsl", False),
        path=info.get("path", ""),
        gpu_acceleration=info.get("gpu_acceleration", False)
    )


def scan_orca() -> SoftwareOrca:
    """Scan and verify ORCA presence on Windows or PATH."""
    orca_exe = shutil.which("orca") or shutil.which("orca.exe")
    if not orca_exe:
        for candidate in [Path("C:/orca"), Path("C:/Program Files/orca")]:
            if candidate.exists() and (candidate / "orca.exe").exists():
                orca_exe = str(candidate / "orca.exe")
                break

    if orca_exe:
        return SoftwareOrca(
            available=True,
            version="Detected",
            path=str(orca_exe)
        )
    return SoftwareOrca(available=False, version="Not installed", path="")


def scan_comsol() -> SoftwareComsol:
    """Scan and verify COMSOL Multiphysics presence on Windows."""
    comsol_root = Path("C:/Program Files/COMSOL")
    if comsol_root.exists():
        for sub in sorted(comsol_root.glob("COMSOL*"), reverse=True):
            batch_exe = sub / "Multiphysics" / "bin" / "win64" / "comsolbatch.exe"
            if batch_exe.exists():
                ver = sub.name.replace("COMSOL", "")
                return SoftwareComsol(
                    available=True,
                    version=ver or "Detected",
                    path=str(batch_exe)
                )

    comsol_exe = shutil.which("comsolbatch") or shutil.which("comsolbatch.exe")
    if comsol_exe:
        return SoftwareComsol(available=True, version="Detected", path=comsol_exe)

    return SoftwareComsol(available=False, version="Not installed", path="")


def scan_python_packages() -> list[str]:
    """Check common scientific chemical computing packages."""
    pkgs = ["numpy", "scipy", "rdkit", "torch", "ase", "matplotlib", "pandas"]
    found = []
    for pkg in pkgs:
        try:
            __import__(pkg)
            found.append(pkg)
        except ImportError:
            pass
    return found


def scan_software() -> SoftwareCatalog:
    """Run full software detection scan."""
    return SoftwareCatalog(
        gromacs=scan_gromacs(),
        orca=scan_orca(),
        comsol=scan_comsol(),
        python_version=sys.version.split()[0],
        installed_packages=scan_python_packages()
    )
