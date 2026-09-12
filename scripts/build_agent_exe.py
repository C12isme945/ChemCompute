"""
PyInstaller build script to compile ChemComputeAgent.exe.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = ROOT / "scripts" / "start_agent.py"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def build_exe():
    print(f"[ChemCompute] Building ChemComputeAgent.exe via PyInstaller...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name", "ChemComputeAgent",
        "--paths", str(ROOT),
        "--hidden-import", "pydantic",
        "--hidden-import", "psutil",
        "--hidden-import", "httpx",
        "--hidden-import", "chemcompute.common.models",
        "--hidden-import", "chemcompute.adapters.gromacs",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        str(ENTRY_POINT)
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode == 0:
        print(f"[OK] ChemComputeAgent built successfully in {DIST_DIR / 'ChemComputeAgent'}")
    else:
        print(f"[FAIL] PyInstaller build failed with returncode {proc.returncode}")


if __name__ == "__main__":
    build_exe()
