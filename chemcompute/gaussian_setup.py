"""ChemCompute Gaussian 本地受控安装与配置服务模块

设计约束与边界说明：
1. 本模块绝不下载、分发或捆绑 Gaussian、GaussView 或任何商业闭源软件。
2. 本模块绝不宣称已验证商业许可，用户必须自行从 Gaussian, Inc. 获取合法授权许可。
3. 检查安装环境 (inspect_installation)：
   - 静态校验指定路径是否为已存在的合法 g09.exe 或 g16.exe。
   - 检查同目录伴侣工具 (g09w.exe, gview.exe, formchk.exe, cubegen.exe) 存在性。
   - 绝对不执行任何探测二进制（避免触发联网许可校验或未预期运行）。
4. 启动安装程序 (launch_installer)：
   - 必须显式传入 licensed=True 确认用户已知晓并持有商业授权。
   - 严格限定仅在 Windows 环境下运行已存在的用户指定 .exe 或 .msi 文件。
   - 交互式启动，绝不进行隐式静默参数猜测 (/qn, /quiet 等)。
   - MSI 安装必须通过绝对受信任的系统 msiexec.exe 路径调用。
5. 节点配置写入 (configure_node)：
   - 若桌面后端 desktop_backend 正在运行，则直接拒绝修改。
   - 载入并仅更新 NodeConfig 中的 gaussian_custom_path，使用 secure save_yaml_config 完整保留其他所有字段。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from chemcompute import desktop_backend
from chemcompute.common.config import NodeConfig, load_yaml_config, save_yaml_config
from chemcompute.node.adapters.gaussian import GaussianAdapter

logger = logging.getLogger(__name__)

# 支持的 Gaussian 主执行文件名 (仅 Windows 二进制校验)
VALID_EXECUTABLE_NAMES = {"g09.exe", "g16.exe"}

# 同目录下常见的辅助与伴侣工具清单
COMPANION_TOOLS = ("g09w.exe", "gview.exe", "formchk.exe", "cubegen.exe")


def inspect_installation(executable: Path | str) -> dict:
    """静态检查本地 Gaussian 安装路径。

    仅进行文件系统层面的存在性与文件名校验，绝对不执行二进制文件。
    返回主执行程序路径与同目录下 companion 工具 (g09w.exe/gview.exe/formchk.exe/cubegen.exe) 存在状态。
    """
    p = Path(executable).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Gaussian 可执行文件不存在: {p}")
    if not p.is_file():
        raise ValueError(f"Gaussian 可执行文件路径不是有效文件: {p}")

    exe_name_lower = p.name.lower()
    if exe_name_lower not in VALID_EXECUTABLE_NAMES:
        raise ValueError(
            f"非法的 Gaussian 可执行文件名 '{p.name}'。仅支持 g09.exe 或 g16.exe。"
        )

    install_dir = p.parent
    dir_files = (
        {entry.name.lower() for entry in install_dir.iterdir() if entry.is_file()}
        if install_dir.is_dir()
        else set()
    )

    tools_presence: dict[str, bool] = {
        tool: tool.lower() in dir_files for tool in COMPANION_TOOLS
    }

    # 调用 GaussianAdapter 静态探测 (不启动任何进程)
    adapter = GaussianAdapter(custom_executable_path=p)
    probe_result = adapter.probe()

    return {
        "valid": True,
        "executable": str(p),
        "executable_path": str(p),
        "name": p.name,
        "directory": str(install_dir),
        "tools": tools_presence,
        "g09w.exe": tools_presence["g09w.exe"],
        "gview.exe": tools_presence["gview.exe"],
        "formchk.exe": tools_presence["formchk.exe"],
        "cubegen.exe": tools_presence["cubegen.exe"],
        "probe": probe_result,
    }


def launch_installer(path: Path | str, licensed: bool) -> subprocess.Popen:
    """交互式启动用户自选的官方 Gaussian/GaussView 安装程序。

    安全约束：
    - 必须显式声明 licensed=True。
    - 仅限 Windows 平台。
    - 安装文件必须存在且扩展名为 .exe 或 .msi。
    - 交互式启动，不猜测静默安装参数。
    - MSI 必须通过 Windows 系统受信任的绝对路径 msiexec.exe 启动。
    """
    if licensed is not True:
        raise ValueError(
            "启动 Gaussian 安装程序前必须明确确认持有合法授权许可 (licensed=True)。"
            "ChemCompute 不分发商业软件，亦不承担许可合规责任。"
        )

    if sys.platform != "win32":
        raise RuntimeError("Gaussian 安装程序仅支持在 Windows 操作系统下启动。")

    installer_path = Path(path).resolve()
    if not installer_path.exists() or not installer_path.is_file():
        raise FileNotFoundError(f"安装程序文件未找到: {installer_path}")

    ext = installer_path.suffix.lower()
    if ext not in {".exe", ".msi"}:
        raise ValueError(
            f"不支持的安装程序文件类型 '{installer_path.suffix}'。仅支持 .exe 或 .msi。"
        )

    if ext == ".msi":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        msiexec_path = Path(system_root) / "System32" / "msiexec.exe"
        if not msiexec_path.is_file():
            raise FileNotFoundError(f"未找到受信任的系统 msiexec.exe (路径: {msiexec_path})")
        # 交互式执行 MSI 安装，绝不添加静默参数
        cmd = [str(msiexec_path), "/i", str(installer_path)]
    else:
        # 交互式执行 EXE 安装，绝不添加静默参数
        cmd = [str(installer_path)]

    logger.info("启动 Gaussian 交互式安装向导: %s", cmd)
    try:
        return subprocess.Popen(cmd, cwd=str(installer_path.parent), shell=False)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 740 and ext == '.exe':
            os.startfile(str(installer_path), 'runas')
            return None
        raise


def configure_node(
    executable: Path | str,
    config_path: Path | str = "config/node.yaml",
) -> NodeConfig:
    """受控配置计算节点的 Gaussian 自定义路径。

    安全约束：
    - 运行时保护：当 desktop_backend 存在活跃运行进程时，拒绝修改配置以防状态冲突。
    - 路径校验：调用 inspect_installation 确保 executable 为合法的 g09.exe/g16.exe。
    - 配置保留：通过 NodeConfig 加载并仅更新 gaussian_custom_path，使用 secure save_yaml_config 保存，
      严格保留其他所有既有字段。
    """
    if desktop_backend.running_process() is not None:
        raise RuntimeError(
            "无法在桌面后台服务正在运行时修改节点配置，请先停止后台服务。"
        )

    inspection = inspect_installation(executable)
    valid_path = inspection["executable"]

    config = load_yaml_config(config_path, NodeConfig)
    config.gaussian_custom_path = valid_path
    save_yaml_config(config, config_path)
    return config
