"""
Unit tests for GROMACS adapter detection, path conversion, and execution lifecycle.
"""

import shutil
from pathlib import Path
from chemcompute.adapters.gromacs import GromacsAdapter, win_to_wsl_path


def test_win_to_wsl_path():
    p = Path("E:/Research/ChemCompute/workspace/job1")
    wsl_p = win_to_wsl_path(p)
    assert wsl_p.startswith("/mnt/e/Research/ChemCompute")
    assert "\\" not in wsl_p


def test_gromacs_detect():
    adapter = GromacsAdapter()
    info = adapter.detect()
    assert "available" in info
    assert "version" in info
    assert "is_wsl" in info
    if info["available"]:
        assert info["version"] != "Unknown"


def test_gromacs_prepare_run_collect(tmp_path: Path):
    src_dir = Path(__file__).resolve().parent.parent / "examples" / "test_gromacs_job"
    # Copy mdp, gro, top to tmp_path
    for f in ["min.mdp", "conf.gro", "topol.top"]:
        src_file = src_dir / f
        if src_file.exists():
            shutil.copy(src_file, tmp_path / f)

    adapter = GromacsAdapter()
    if not adapter.detect()["available"]:
        return

    # 1. Prepare
    ok_prep = adapter.prepare(tmp_path, {})
    assert ok_prep is True
    assert (tmp_path / "run.tpr").exists()

    # 2. Run
    progress_records = []
    def on_progress(pct, line):
        progress_records.append((pct, line))

    ok_run = adapter.run(tmp_path, {"nsteps": 50}, progress_callback=on_progress)
    assert ok_run is True
    assert len(progress_records) > 0

    # 3. Collect
    results = adapter.collect(tmp_path)
    assert len(results) == 1
    assert results[0].name == "results.zip"
    assert results[0].stat().st_size > 0
