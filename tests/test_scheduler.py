"""
Unit tests for Database and Smart Scheduler logic.
"""

import time
from pathlib import Path
from chemcompute.controller.database import Database
from chemcompute.controller.scheduler import Scheduler
from chemcompute.common.models import (
    NodeRole,
    NodeState,
    NodeTelemetry,
    HardwareCPU,
    HardwareRAM,
    HardwareGPU,
    SoftwareCatalog,
    SoftwareGromacs,
    JobRequirements,
    JobStatus
)


def test_scheduler_matching(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    scheduler = Scheduler(db)

    # 1. Setup Node A: CPU only (Online)
    node_a_tel = NodeTelemetry(
        cpu=HardwareCPU(logical_cores=16, utilization_pct=15.0),
        ram=HardwareRAM(total_mb=32768, available_mb=20000, used_pct=30.0),
        gpu=None,
        timestamp=time.time()
    )
    node_a_soft = SoftwareCatalog(
        gromacs=SoftwareGromacs(available=True, version="2023.3", is_wsl=True)
    )
    db.register_node(
        node_id="node-cpu",
        node_name="Lab-CPU-Node",
        token_hash="hash_a",
        ip_address="192.168.1.10",
        role=NodeRole.WORKER,
        software=node_a_soft,
        telemetry=node_a_tel
    )

    # 2. Setup Node B: GPU RTX 3060 (Online)
    node_b_tel = NodeTelemetry(
        cpu=HardwareCPU(logical_cores=8, utilization_pct=25.0),
        ram=HardwareRAM(total_mb=16384, available_mb=10000, used_pct=40.0),
        gpu=HardwareGPU(
            name="NVIDIA GeForce RTX 3060",
            memory_total_mb=6144,
            memory_free_mb=5000,
            memory_used_mb=1144,
            utilization_gpu_pct=10.0
        ),
        timestamp=time.time()
    )
    node_b_soft = SoftwareCatalog(
        gromacs=SoftwareGromacs(available=True, version="2023.3", is_wsl=True, gpu_acceleration=True)
    )
    db.register_node(
        node_id="node-gpu",
        node_name="Rig-RTX-3060",
        token_hash="hash_b",
        ip_address="192.168.1.20",
        role=NodeRole.WORKER,
        software=node_b_soft,
        telemetry=node_b_tel
    )

    # 3. Create a GROMACS GPU job
    job1 = db.create_job(
        job_id="job-gpu-01",
        name="HA-Equilibration",
        adapter="gromacs",
        requirements=JobRequirements(
            software="gromacs",
            require_gpu=True,
            min_vram_mb=4000
        ),
        parameters={"use_gpu": True}
    )

    # Run scheduler cycle
    assigned_count = scheduler.schedule_cycle()
    assert assigned_count == 1

    # Check that job was assigned specifically to node-gpu
    updated_job = db.get_job("job-gpu-01")
    assert updated_job.status == JobStatus.ASSIGNED
    assert updated_job.assigned_node_id == "node-gpu"

    # 4. Create a CPU-only job
    job2 = db.create_job(
        job_id="job-cpu-02",
        name="Analysis-CPU",
        adapter="gromacs",
        requirements=JobRequirements(software="gromacs", require_gpu=False),
        parameters={"use_gpu": False}
    )

    assigned_count2 = scheduler.schedule_cycle()
    assert assigned_count2 == 1
    updated_job2 = db.get_job("job-cpu-02")
    assert updated_job2.status == JobStatus.ASSIGNED
    assert updated_job2.assigned_node_id == "node-cpu"
