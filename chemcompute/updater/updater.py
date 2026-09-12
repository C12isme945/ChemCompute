"""
ChemCompute Dual-Process Updater.
Performs package download, SHA256 verification, process termination,
atomic directory replacement (current/previous/staging), health checks, and automatic rollback.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import List, Optional
import httpx
import psutil


class ChemComputeUpdater:
    """Independent updater managing the atomic lifecycle and rollback of ChemCompute."""

    def __init__(self, agent_dir: Path, log_file: Optional[Path] = None):
        self.agent_dir = agent_dir
        self.current_dir = agent_dir / "current"
        self.previous_dir = agent_dir / "previous"
        self.staging_dir = agent_dir / "staging"
        self.log_file = log_file or (agent_dir / "updater.log")
        self.agent_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [Updater] {message}"
        print(line)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def compute_sha256(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def download_package(self, package_url: str, dest_path: Path) -> bool:
        self.log(f"Downloading package from {package_url} -> {dest_path}...")
        try:
            with httpx.Client(timeout=120, follow_redirects=True) as client:
                with client.stream("GET", package_url) as resp:
                    if resp.status_code != 200:
                        self.log(f"Download failed with HTTP {resp.status_code}")
                        return False
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_bytes(65536):
                            f.write(chunk)
            self.log(f"Package downloaded successfully ({dest_path.stat().st_size} bytes)")
            return True
        except Exception as ex:
            self.log(f"Download exception: {ex}")
            return False

    def wait_for_process_exit(self, pid: int, timeout: float = 15.0) -> bool:
        """Wait for the old agent process to terminate and release file locks."""
        self.log(f"Waiting for agent PID {pid} to terminate...")
        try:
            proc = psutil.Process(pid)
            # Give it a polite chance to exit
            start = time.time()
            while time.time() - start < timeout:
                if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                    self.log(f"Agent process {pid} has exited.")
                    return True
                time.sleep(0.5)

            self.log(f"Agent process {pid} still running after {timeout}s, terminating forcefully...")
            proc.terminate()
            proc.wait(timeout=5)
            return True
        except psutil.NoSuchProcess:
            return True
        except Exception as ex:
            self.log(f"Error waiting for process {pid}: {ex}")
            return False

    def rollback(self, launch_cmd: Optional[List[str]] = None) -> bool:
        """Roll back current/ directory from previous/ backup and restart old agent."""
        self.log("!!! TRIGGERING AUTOMATIC ROLLBACK !!!")
        try:
            if not self.previous_dir.exists():
                self.log("Cannot rollback: previous/ backup directory does not exist!")
                return False

            failed_dir = self.agent_dir / f"failed_{int(time.time())}"
            if self.current_dir.exists():
                try:
                    self.current_dir.rename(failed_dir)
                except Exception:
                    shutil.rmtree(self.current_dir, ignore_errors=True)

            self.previous_dir.rename(self.current_dir)
            self.log("Restored previous/ to current/ successfully.")

            # Restart previous version
            if launch_cmd:
                self.log(f"Restarting rolled-back agent: {' '.join(launch_cmd)}")
                subprocess.Popen(launch_cmd, cwd=str(self.current_dir))
            return True
        except Exception as ex:
            self.log(f"CRITICAL: Rollback failed with exception: {ex}")
            return False

    def perform_update(
        self,
        package_source: str,
        expected_sha256: str,
        target_version: str,
        agent_pid: Optional[int] = None,
        launch_cmd: Optional[List[str]] = None,
        health_url: Optional[str] = None,
        timeout: float = 30.0
    ) -> bool:
        """
        Main update execution loop:
        1. Download/Verify package
        2. Unpack into staging/
        3. Wait for old Agent PID to exit
        4. Swap: previous <- current <- staging
        5. Start new version
        6. Health check probe
        7. Auto-rollback if health check fails
        """
        self.log(f"=== Starting ChemCompute Update to v{target_version} ===")

        # Step 1: Prepare package file
        package_file = self.agent_dir / f"update_{target_version}.zip"
        if package_source.startswith("http://") or package_source.startswith("https://") or package_source.startswith("/api/"):
            if not self.download_package(package_source, package_file):
                self.log("Update aborted: Failed to download package.")
                return False
        else:
            # Local file path
            local_src = Path(package_source)
            if not local_src.exists():
                self.log(f"Update aborted: Local package file {local_src} does not exist.")
                return False
            shutil.copy2(local_src, package_file)

        # Step 2: Verify SHA256
        actual_sha = self.compute_sha256(package_file)
        if expected_sha256 and actual_sha.lower() != expected_sha256.lower():
            self.log(f"Security check failed! SHA256 mismatch. Expected: {expected_sha256}, Actual: {actual_sha}")
            package_file.unlink(missing_ok=True)
            return False
        self.log(f"SHA256 verified successfully: {actual_sha}")

        # Step 3: Extract into staging
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir, ignore_errors=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(package_file, "r") as zf:
                zf.extractall(self.staging_dir)
            self.log("Extracted new version into staging/ successfully.")
        except Exception as ex:
            self.log(f"Failed to unpack update package: {ex}")
            shutil.rmtree(self.staging_dir, ignore_errors=True)
            package_file.unlink(missing_ok=True)
            return False
        finally:
            package_file.unlink(missing_ok=True)

        # Step 4: Wait for old process to exit
        if agent_pid:
            self.wait_for_process_exit(agent_pid, timeout=15.0)

        # Step 5: Atomic directory rotation
        try:
            if self.previous_dir.exists():
                shutil.rmtree(self.previous_dir, ignore_errors=True)

            if self.current_dir.exists():
                self.current_dir.rename(self.previous_dir)
                self.log("Moved current/ -> previous/")

            self.staging_dir.rename(self.current_dir)
            self.log("Moved staging/ -> current/")
        except Exception as ex:
            self.log(f"Atomic file swap failed: {ex}. Attempting restoration...")
            self.rollback(launch_cmd)
            return False

        # Step 6: Spawn new Agent
        new_proc = None
        if launch_cmd:
            self.log(f"Spawning new Agent: {' '.join(launch_cmd)}")
            try:
                new_proc = subprocess.Popen(launch_cmd, cwd=str(self.current_dir))
            except Exception as ex:
                self.log(f"Failed to launch new agent version: {ex}")
                self.rollback(launch_cmd)
                return False

        # Step 7: Health Check verification
        self.log(f"Running health probe for up to {timeout}s...")
        health_passed = False
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if process died prematurely
            if new_proc and new_proc.poll() is not None:
                self.log(f"New agent process crashed with exit code {new_proc.returncode}!")
                health_passed = False
                break

            if health_url:
                try:
                    with httpx.Client(timeout=2.0) as client:
                        resp = client.get(health_url)
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("status") in ["healthy", "ok"]:
                                self.log(f"Health check probe passed! Response: {data}")
                                health_passed = True
                                break
                except Exception:
                    pass
            else:
                # If no health_url specified, process staying alive for threshold is deemed healthy
                alive_threshold = min(1.5, timeout * 0.5)
                if time.time() - start_time >= alive_threshold:
                    health_passed = True
                    break

            time.sleep(1.0)

        if not health_passed:
            self.log("Health check failed or timed out!")
            if new_proc and new_proc.poll() is None:
                new_proc.terminate()
                try:
                    new_proc.wait(timeout=3)
                except Exception:
                    new_proc.kill()

            self.rollback(launch_cmd)
            return False

        self.log(f"=== ChemCompute v{target_version} Update Completed Successfully! ===")
        return True
