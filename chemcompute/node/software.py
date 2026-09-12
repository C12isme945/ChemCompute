"""ChemCompute 软件环境与资产清单搜集模块"""

from __future__ import annotations

import platform
import socket
import sys
from pathlib import Path

from chemcompute.common.models import SoftwareInfo
from chemcompute.node.adapters.gromacs import GromacsAdapter


def collect_software_info(custom_gromacs_path: str | Path | None = None, gaussian_path: str | None = None) -> SoftwareInfo:
    """搜集操作系统及软件清单 (包含 GROMACS 探测)"""
    adapter = GromacsAdapter(custom_executable_path=custom_gromacs_path)
    gmx_info = adapter.probe()

    from chemcompute.node.adapters.gaussian import GaussianAdapter
    gaussian = GaussianAdapter(gaussian_path).probe()
    return SoftwareInfo(
        hostname=socket.gethostname(),
        os_name=platform.system(),
        os_release=platform.release(),
        os_version=platform.version(),
        architecture=platform.machine(),
        python_version=sys.version.split()[0],
        gromacs=gmx_info,
        gaussian=gaussian,
    )
