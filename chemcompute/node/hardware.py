"""ChemCompute 硬件资产探测模块 (psutil + nvidia-smi)

特性：
- 采集精确的 CPU/内存/磁盘度量
- 安全、鲁棒探测 NVIDIA GPU (若未安装驱动或无卡则无损静默降级)
- 严禁 Shell 管道，使用安全列表化参数调用
"""

from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import subprocess

import psutil

from chemcompute.common.models import GpuDevice, HardwareInfo

logger = logging.getLogger(__name__)


def probe_nvidia_gpus() -> list[GpuDevice]:
    """探测 NVIDIA GPU 显卡硬件清单与显存/温度指标 (鲁棒容错)"""
    nvidia_smi_path = shutil.which("nvidia-smi")
    if not nvidia_smi_path:
        # 在 Windows 上尝试标准默认路径
        candidates = [
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                nvidia_smi_path = c
                break

    if not nvidia_smi_path:
        logger.debug("系统中未检测到 nvidia-smi 实用程序，跳过 GPU 探测")
        return []

    cmd = [
        nvidia_smi_path,
        "--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu,utilization.memory,driver_version",
        "--format=csv,noheader,nounits",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        if proc.returncode != 0:
            logger.debug("nvidia-smi 返回非零码 %d: %s", proc.returncode, proc.stderr.strip())
            return []

        devices: list[GpuDevice] = []
        reader = csv.reader(io.StringIO(proc.stdout.strip()))
        for row in reader:
            if not row or len(row) < 9:
                continue
            try:
                idx = int(row[0].strip())
                name = row[1].strip()
                total_mb = int(float(row[2].strip()))
                used_mb = int(float(row[3].strip()))
                free_mb = int(float(row[4].strip()))
                temp = int(float(row[5].strip())) if row[5].strip() != "[N/A]" else None
                util_gpu = int(float(row[6].strip())) if row[6].strip() != "[N/A]" else None
                util_mem = int(float(row[7].strip())) if row[7].strip() != "[N/A]" else None
                driver = row[8].strip()

                devices.append(
                    GpuDevice(
                        index=idx,
                        name=name,
                        total_memory_mb=total_mb,
                        used_memory_mb=used_mb,
                        free_memory_mb=free_mb,
                        temperature_c=temp,
                        utilization_gpu_percent=util_gpu,
                        utilization_mem_percent=util_mem,
                        driver_version=driver,
                    )
                )
            except Exception as parse_err:
                logger.debug("解析 GPU 记录行失败 %s: %s", row, parse_err)

        return devices
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, Exception) as e:
        logger.debug("执行 nvidia-smi 失败: %s", e)
        return []


def collect_hardware_info() -> HardwareInfo:
    """搜集当前节点的完整硬件清单"""
    # 1. CPU
    cpu_logical = psutil.cpu_count(logical=True) or 1
    cpu_physical = psutil.cpu_count(logical=False) or 1
    cpu_pct = float(psutil.cpu_percent(interval=0.1))

    cpu_freq_val: float | None = None
    try:
        freq = psutil.cpu_freq()
        if freq and freq.current:
            cpu_freq_val = round(float(freq.current), 1)
    except Exception:
        pass

    # 2. RAM
    mem = psutil.virtual_memory()
    ram_total_mb = int(mem.total // (1024 * 1024))
    ram_avail_mb = int(mem.available // (1024 * 1024))
    ram_pct = float(mem.percent)

    # 3. Disk
    try:
        disk_target = os.path.splitdrive(os.getcwd())[0] + "\\" if os.name == "nt" else "/"
        disk = psutil.disk_usage(disk_target)
        disk_total_gb = round(disk.total / (1024**3), 1)
        disk_free_gb = round(disk.free / (1024**3), 1)
        disk_pct = float(disk.percent)
    except Exception:
        disk_total_gb = 0.0
        disk_free_gb = 0.0
        disk_pct = 0.0

    # 4. GPU
    gpus = probe_nvidia_gpus()

    return HardwareInfo(
        cpu_count_logical=cpu_logical,
        cpu_count_physical=cpu_physical,
        cpu_percent=cpu_pct,
        cpu_frequency_mhz=cpu_freq_val,
        ram_total_mb=ram_total_mb,
        ram_available_mb=ram_avail_mb,
        ram_percent=ram_pct,
        disk_total_gb=disk_total_gb,
        disk_free_gb=disk_free_gb,
        disk_percent=disk_pct,
        gpus=gpus,
    )
