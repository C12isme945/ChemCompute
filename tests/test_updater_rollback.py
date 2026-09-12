"""
Tests for ChemCompute Dual-Process Updater and Automatic Rollback mechanism.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
import pytest

from chemcompute.updater.updater import ChemComputeUpdater


def create_mock_package(dest_zip: Path, version_str: str, fail_on_run: bool = False):
    """Creates a mock update package zip."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        ver_file = tmp_dir / "version.txt"
        ver_file.write_text(version_str, encoding="utf-8")

        run_script = tmp_dir / "run.py"
        if fail_on_run:
            # Script exits immediately with error (simulates crash)
            run_script.write_text("import sys; sys.exit(1)", encoding="utf-8")
        else:
            # Script runs healthy
            run_script.write_text("import time; time.sleep(10)", encoding="utf-8")

        with zipfile.ZipFile(dest_zip, "w") as zf:
            zf.write(ver_file, "version.txt")
            zf.write(run_script, "run.py")


def test_sha256_verification_and_tamper_detection(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    updater = ChemComputeUpdater(agent_dir=agent_dir)

    pkg = tmp_path / "pkg_valid.zip"
    create_mock_package(pkg, "0.2.0")

    # Correct sha256
    correct_sha = updater.compute_sha256(pkg)

    # Corrupt/Wrong sha256 test
    res = updater.perform_update(
        package_source=str(pkg),
        expected_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        target_version="0.2.0"
    )
    assert res is False, "Updater should reject package with mismatched SHA256!"


def test_successful_atomic_swap(tmp_path):
    agent_dir = tmp_path / "agent"
    current_dir = agent_dir / "current"
    current_dir.mkdir(parents=True)
    (current_dir / "version.txt").write_text("0.1.0", encoding="utf-8")

    pkg = tmp_path / "pkg_020.zip"
    create_mock_package(pkg, "0.2.0", fail_on_run=False)

    updater = ChemComputeUpdater(agent_dir=agent_dir)
    sha = updater.compute_sha256(pkg)

    # Update with command that stays alive
    cmd = [sys.executable, "-c", "import time; time.sleep(15)"]
    success = updater.perform_update(
        package_source=str(pkg),
        expected_sha256=sha,
        target_version="0.2.0",
        launch_cmd=cmd,
        timeout=3.0
    )

    assert success is True
    assert (agent_dir / "current" / "version.txt").read_text(encoding="utf-8") == "0.2.0"
    assert (agent_dir / "previous" / "version.txt").read_text(encoding="utf-8") == "0.1.0"


def test_automatic_rollback_on_failure(tmp_path):
    """
    Simulate new version crashing upon startup.
    Updater must catch the failure, restore previous/ back to current/,
    and re-launch old version!
    """
    agent_dir = tmp_path / "agent"
    current_dir = agent_dir / "current"
    current_dir.mkdir(parents=True)
    (current_dir / "version.txt").write_text("0.1.0", encoding="utf-8")

    # Broken update package that exits with 1
    pkg = tmp_path / "pkg_broken.zip"
    create_mock_package(pkg, "0.2.0-broken", fail_on_run=True)

    updater = ChemComputeUpdater(agent_dir=agent_dir)
    sha = updater.compute_sha256(pkg)

    # Launch command that will fail immediately
    failing_cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
    success = updater.perform_update(
        package_source=str(pkg),
        expected_sha256=sha,
        target_version="0.2.0-broken",
        launch_cmd=failing_cmd,
        timeout=3.0
    )

    # Update should report failure
    assert success is False

    # Rollback verification: current/ must be restored to 0.1.0!
    assert (agent_dir / "current" / "version.txt").read_text(encoding="utf-8") == "0.1.0"
    print("\n[OK] Rollback test passed: Version restored to 0.1.0 successfully!")
