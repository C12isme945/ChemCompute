"""
Unit tests for hardware telemetry and software scanner.
"""

from chemcompute.agent.hardware import probe_cpu, probe_ram, probe_gpu, probe_hardware
from chemcompute.agent.software_scanner import scan_software


def test_probe_cpu():
    cpu = probe_cpu()
    assert cpu.logical_cores >= 1
    assert cpu.physical_cores >= 1
    assert isinstance(cpu.model, str)
    assert 0.0 <= cpu.utilization_pct <= 100.0


def test_probe_ram():
    ram = probe_ram()
    assert ram.total_mb > 0.0
    assert ram.available_mb > 0.0
    assert 0.0 <= ram.used_pct <= 100.0


def test_probe_gpu():
    # If NVIDIA GPU exists on this machine, test fields
    gpu = probe_gpu()
    if gpu is not None:
        assert gpu.name != ""
        assert gpu.memory_total_mb > 0.0
        assert 0.0 <= gpu.utilization_gpu_pct <= 100.0


def test_probe_hardware_telemetry():
    tel = probe_hardware()
    assert tel.cpu is not None
    assert tel.ram is not None
    assert tel.timestamp > 0.0


def test_scan_software():
    sw = scan_software()
    assert sw.python_version != ""
    assert sw.gromacs is not None
    print(f"Software scan: GROMACS={sw.gromacs.available} (v{sw.gromacs.version}, WSL={sw.gromacs.is_wsl})")
