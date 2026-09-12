"""
GROMACS Runtime Adapter for ChemCompute.
Supports Windows native binaries, Windows batch scripts, and WSL2 GROMACS instances.
"""

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional, Tuple

from chemcompute.adapters.base import BaseAdapter


def win_to_wsl_path(path: Path | str) -> str:
    """Convert a Windows absolute path (e.g. E:\\Research\\ChemCompute) to WSL path (/mnt/e/Research/ChemCompute)."""
    s = str(path).replace("\\", "/")
    if ":" in s:
        parts = s.split(":", 1)
        drive = parts[0].strip().replace("/", "").lower()
        subpath = parts[1].lstrip("/")
        return f"/mnt/{drive}/{subpath}"
    if s.startswith("/mnt/"):
        return s
    return s


class GromacsAdapter(BaseAdapter):
    """
    GROMACS MD simulation adapter.
    Handles input verification, grompp preprocessing, mdrun execution,
    GPU acceleration flags, live step tracking, and result packaging.
    """

    def __init__(self):
        super().__init__(name="gromacs")
        self.executable: str = ""
        self.is_wsl: bool = False
        self.version: str = "Unknown"
        self.gpu_support: bool = False
        self._cached_detection: Optional[Dict[str, Any]] = None

    def detect(self) -> Dict[str, Any]:
        """Detect GROMACS installation across Windows and WSL."""
        if self._cached_detection is not None:
            return self._cached_detection

        result = {
            "available": False,
            "version": "Unknown",
            "is_wsl": False,
            "path": "",
            "gpu_acceleration": False
        }

        # 1. Check native Windows gmx
        gmx_win = shutil.which("gmx") or shutil.which("gmx.exe") or shutil.which("gmx.cmd")
        if gmx_win:
            try:
                proc = subprocess.run(
                    [gmx_win, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                output = proc.stdout
                ver_match = re.search(r"GROMACS version:\s*([\w\.\-]+)", output, re.IGNORECASE)
                version = ver_match.group(1) if ver_match else "detected"
                gpu_acc = bool(re.search(r"GPU support:\s*enabled", output)) or "CUDA" in output
                is_wsl_wrap = "Ubuntu" in output or "wsl" in gmx_win.lower()

                self.executable = gmx_win
                self.is_wsl = is_wsl_wrap
                self.version = version
                self.gpu_support = gpu_acc
                result = {
                    "available": True,
                    "version": version,
                    "is_wsl": is_wsl_wrap,
                    "path": gmx_win,
                    "gpu_acceleration": gpu_acc
                }
                self._cached_detection = result
                return result
            except Exception:
                pass

        # 2. Check direct WSL gmx
        if shutil.which("wsl"):
            try:
                proc = subprocess.run(
                    ["wsl", "gmx", "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                output = proc.stdout
                if proc.returncode == 0 and "GROMACS" in output:
                    ver_match = re.search(r"GROMACS version:\s*([\w\.\-]+)", output, re.IGNORECASE)
                    version = ver_match.group(1) if ver_match else "WSL-detected"
                    gpu_acc = bool(re.search(r"GPU support:\s*enabled", output)) or "CUDA" in output
                    self.executable = "wsl gmx"
                    self.is_wsl = True
                    self.version = version
                    self.gpu_support = gpu_acc
                    result = {
                        "available": True,
                        "version": version,
                        "is_wsl": True,
                        "path": "wsl gmx",
                        "gpu_acceleration": gpu_acc
                    }
                    self._cached_detection = result
                    return result
            except Exception:
                pass

        self._cached_detection = result
        return result

    def _build_command(self, subcmd: str, args: List[str], cwd: Path) -> Tuple[List[str], Optional[str]]:
        """Construct command accounting for WSL or native Windows."""
        det = self.detect()
        if det["is_wsl"]:
            wsl_cwd = win_to_wsl_path(cwd)
            # When using WSL, we can run inside wsl and cd to wsl_cwd
            # or pass wsl args directly
            cmd = ["wsl", "bash", "-c", f"cd '{wsl_cwd}' && gmx {subcmd} " + " ".join(f"'{a}'" for a in args)]
            return cmd, None
        else:
            exe = self.executable or "gmx"
            return [exe, subcmd] + args, str(cwd)

    def prepare(self, workspace_dir: Path, parameters: Dict[str, Any]) -> bool:
        """
        Verify or generate run.tpr in workspace_dir.
        If run.tpr or topol.tpr already exists, returns True.
        Otherwise, looks for *.mdp, *.gro, *.top and runs grompp.
        """
        self.detect()
        tpr_files = list(workspace_dir.glob("*.tpr"))
        if tpr_files:
            return True

        # Need to compile tpr with grompp
        mdp_files = list(workspace_dir.glob("*.mdp"))
        gro_files = list(workspace_dir.glob("*.gro"))
        top_files = list(workspace_dir.glob("*.top"))

        if not (mdp_files and gro_files and top_files):
            return False

        mdp = mdp_files[0].name
        gro = gro_files[0].name
        top = top_files[0].name
        out_tpr = "run.tpr"

        grompp_args = ["-f", mdp, "-c", gro, "-p", top, "-o", out_tpr, "-maxwarn", "2"]
        cmd, cwd = self._build_command("grompp", grompp_args, workspace_dir)

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            return proc.returncode == 0 and (workspace_dir / out_tpr).exists()
        except Exception:
            return False

    def run(
        self,
        workspace_dir: Path,
        parameters: Dict[str, Any],
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """
        Execute GROMACS mdrun in workspace_dir with real-time step tracking.
        """
        det = self.detect()
        if not det["available"]:
            if progress_callback:
                progress_callback(0.0, "[ERROR] GROMACS is not installed or available on this node.")
            return False

        tpr_files = list(workspace_dir.glob("*.tpr"))
        if not tpr_files:
            if not self.prepare(workspace_dir, parameters):
                if progress_callback:
                    progress_callback(0.0, "[ERROR] Failed to find or generate .tpr file for GROMACS simulation.")
                return False
            tpr_files = list(workspace_dir.glob("*.tpr"))

        tpr_name = tpr_files[0].stem
        deffnm = parameters.get("deffnm", tpr_name)

        mdrun_args = ["-deffnm", deffnm, "-v"]

        # GPU acceleration options
        use_gpu = parameters.get("use_gpu", False)
        if use_gpu and det.get("gpu_acceleration", False):
            mdrun_args.extend(["-nb", "gpu", "-pme", "gpu"])
        elif use_gpu:
            # Try GPU offloading if user asked
            mdrun_args.extend(["-nb", "gpu"])

        # Threads configuration if provided
        threads = parameters.get("threads")
        if threads:
            mdrun_args.extend(["-nt", str(threads)])

        # Max steps or custom flags
        nsteps = parameters.get("nsteps", 0)

        cmd, cwd = self._build_command("mdrun", mdrun_args, workspace_dir)

        if progress_callback:
            progress_callback(1.0, f"[INIT] Starting GROMACS mdrun: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )

            current_step = 0
            total_steps = nsteps

            # Try to read nsteps from mdp if not provided
            if total_steps <= 0:
                for mdp_path in workspace_dir.glob("*.mdp"):
                    try:
                        content = mdp_path.read_text(errors="ignore")
                        m = re.search(r"^\s*nsteps\s*=\s*(\d+)", content, re.MULTILINE | re.IGNORECASE)
                        if m:
                            total_steps = int(m.group(1))
                            break
                    except Exception:
                        pass

            for line in iter(proc.stdout.readline, ''):
                line_str = line.strip()
                if not line_str:
                    continue

                # Parse Step X of Y or Step X, time Y
                step_match = re.search(r"Step\s+(\d+)", line_str, re.IGNORECASE)
                if step_match:
                    current_step = int(step_match.group(1))
                    if total_steps > 0:
                        pct = min(99.0, max(1.0, round((current_step / total_steps) * 100.0, 1)))
                    else:
                        pct = min(90.0, 5.0 + (current_step % 85))
                    if progress_callback:
                        progress_callback(pct, line_str)
                else:
                    if progress_callback and ("Writing checkpoint" in line_str or "Fatal error" in line_str or "Started mdrun" in line_str):
                        progress_callback(50.0 if current_step == 0 else 0.0, line_str)

            proc.stdout.close()
            returncode = proc.wait()

            if returncode == 0:
                if progress_callback:
                    progress_callback(100.0, "[SUCCESS] GROMACS simulation completed successfully.")
                return True
            else:
                if progress_callback:
                    progress_callback(0.0, f"[ERROR] GROMACS mdrun exited with return code {returncode}.")
                return False

        except Exception as ex:
            if progress_callback:
                progress_callback(0.0, f"[EXCEPTION] Failed to execute GROMACS mdrun: {str(ex)}")
            return False

    def collect(self, workspace_dir: Path) -> List[Path]:
        """
        Gather GROMACS trajectory, energy, checkpoint, and log files.
        Compress them into results.zip in workspace_dir.
        """
        extensions = {".edr", ".xtc", ".trr", ".gro", ".log", ".cpt", ".tpr"}
        result_files: List[Path] = []
        for file_path in workspace_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                result_files.append(file_path)

        zip_path = workspace_dir / "results.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for rf in result_files:
                zipf.write(rf, arcname=rf.name)

        return [zip_path]
