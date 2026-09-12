"""
Hardware telemetry probe for ChemCompute Node.
Collects CPU, RAM, and NVIDIA GPU metrics.
"""

import os
import platform
import subprocess
import time
from typing import Optional
import psutil

from chemcompute.common.models import HardwareCPU, HardwareRAM, HardwareGPU, NodeTelemetry


def get_cpu_model_name() -> str:
    """Retrieve readable CPU brand name on Windows/Linux."""
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return model.strip()
        except Exception:
            pass
    return platform.processor() or "Unknown CPU"


def probe_cpu() -> HardwareCPU:
    """Collect CPU statistics."""
    model = get_cpu_model_name()
    physical_cores = psutil.cpu_count(logical=False) or 1
    logical_cores = psutil.cpu_count(logical=True) or 1
    util_pct = psutil.cpu_percent(interval=None)
    freq = psutil.cpu_freq()
    freq_mhz = freq.current if freq else 0.0

    return HardwareCPU(
        model=model,
        physical_cores=physical_cores,
        logical_cores=logical_cores,
        utilization_pct=float(util_pct),
        frequency_mhz=float(freq_mhz)
    )


def probe_ram() -> HardwareRAM:
    """Collect RAM statistics."""
    mem = psutil.virtual_memory()
    return HardwareRAM(
        total_mb=round(mem.total / (1024 * 1024), 1),
        available_mb=round(mem.available / (1024 * 1024), 1),
        used_pct=float(mem.percent)
    )


def probe_gpu() -> Optional[HardwareGPU]:
    """Probe NVIDIA GPU metrics via nvidia-smi."""
    try:
        query_fields = "name,driver_version,memory.total,memory.free,memory.used,utilization.gpu,temperature.gpu"
        cmd = [
            "nvidia-smi",
            f"--query-gpu={query_fields}",
            "--format=csv,noheader,nounits"
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        if proc.returncode != 0:
            return None

        line = proc.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            return None

        # Determine CUDA version from full nvidia-smi header
        cuda_ver = None
        try:
            full_proc = subprocess.run(
                ["nvidia-smi"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            for l in full_proc.stdout.split("\n"):
                if "CUDA Version:" in l:
                    cuda_ver = l.split("CUDA Version:")[1].split()[0].strip()
                    break
        except Exception:
            pass

        return HardwareGPU(
            name=parts[0],
            driver_version=parts[1],
            memory_total_mb=float(parts[2]),
            memory_free_mb=float(parts[3]),
            memory_used_mb=float(parts[4]),
            utilization_gpu_pct=float(parts[5]),
            temperature_c=float(parts[6]),
            cuda_version=cuda_ver
        )
    except Exception:
        return None


def probe_hardware() -> NodeTelemetry:
    """Probe full system hardware telemetry."""
    return NodeTelemetry(
        cpu=probe_cpu(),
        ram=probe_ram(),
        gpu=probe_gpu(),
        timestamp=time.time()
    )
