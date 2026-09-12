"""
Windows Background Service and Auto-start Helper for ChemCompute Agent.
Allows ChemCompute Agent to run unattended across system reboots.
"""

import os
import subprocess
import sys
from pathlib import Path


def register_windows_task(agent_py_path: Path, task_name: str = "ChemComputeAgent") -> bool:
    """
    Register ChemCompute Agent in Windows Task Scheduler to run at system startup
    with highest privileges and unattended execution.
    """
    python_exe = sys.executable
    script_path = str(agent_py_path.resolve())

    # Create schtasks command
    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", f'"{python_exe}" "{script_path}"',
        "/SC", "ONSTART",
        "/RU", "SYSTEM",
        "/F"
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc.returncode == 0
    except Exception:
        return False


def unregister_windows_task(task_name: str = "ChemComputeAgent") -> bool:
    """Remove scheduled startup task."""
    cmd = ["schtasks", "/Delete", "/TN", task_name, "/F"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc.returncode == 0
    except Exception:
        return False
