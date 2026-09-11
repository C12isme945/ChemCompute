"""ChemCompute GROMACS 安全适配器

安全约束与边界设计：
1. 真实可执行文件探测：深度遍历标准路径及 PATH，读取版本及 GPU/MPI 支持
2. 有界受限的子进程调用：
   - 绝不使用 shell=True，严防 Shell 注入
   - 严格限定白名单子命令 (version, check, grompp, mdrun, editconf, solvate, genion, energy)
   - 过滤非法元字符 (; & | ` $ < > \r \n)
   - 路径穿越防御 (确保操作限定在专用 workspace 目录内)
   - 强行超时边界控制 (避免作业挂起耗尽计算资源)
   - 内存与输出缓冲区截断 (防止海量输出导致 OOM)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from chemcompute.common.models import GromacsSoftwareInfo

logger = logging.getLogger(__name__)

# 严格允许的 GROMACS 子命令集合
ALLOWED_SUBCOMMANDS = {
    "version",
    "check",
    "grompp",
    "mdrun",
    "editconf",
    "solvate",
    "genion",
    "energy",
}

# 危险字符匹配：禁止管道、重定向、命令级联、空字符
FORBIDDEN_CHAR_REGEX = re.compile(r"[;&|`$<>\r\n\0]")

# 路径穿越防御正则：禁止引用父级目录
PATH_TRAVERSAL_REGEX = re.compile(r"(\.\.[/\\]|[/\\]\.\.|^\.\.$)")


@dataclass
class ExecutionResult:
    """子进程受控执行结果封装"""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    error_message: str | None = None


class GromacsAdapter:
    """GROMACS 安全执行适配器"""

    def __init__(
        self,
        custom_executable_path: str | Path | None = None,
        workspace_dir: str | Path = "data/workspace",
        max_timeout_seconds: int = 3600,
    ) -> None:
        self.custom_path = Path(custom_executable_path) if custom_executable_path else None
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.max_timeout = max_timeout_seconds
        self._cached_info: GromacsSoftwareInfo | None = None
        self._wsl = False

    def probe(self, force_refresh: bool = False) -> GromacsSoftwareInfo:
        """真实探测主机中安装的 GROMACS 可执行文件及其编译特性"""
        if self._cached_info and not force_refresh:
            return self._cached_info

        exe_path = self._find_executable()
        self._wsl = False
        if not exe_path and os.name == "nt":
            wsl = shutil.which("wsl.exe")
            if wsl:
                exe_path = Path(wsl)
                self._wsl = True
        if not exe_path:
            self._cached_info = GromacsSoftwareInfo(
                found=False,
                probe_output="系统中未检测到 GROMACS 可执行程序 (已检索 PATH 及常见默认安装目录)",
            )
            return self._cached_info

        # 执行 gmx --version 获取详细编译特性
        try:
            cmd = ([str(exe_path), "--exec", "gmx", "--version"] if self._wsl
                   else [str(exe_path), "--version"])
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
            raw_out = (proc.stdout + "\n" + proc.stderr).strip()
            if proc.returncode != 0 or "GROMACS version:" not in raw_out:
                self._cached_info = GromacsSoftwareInfo(found=False, probe_output=raw_out[:1000])
                return self._cached_info

            info = self._parse_version_output(raw_out, str(exe_path))
            self._cached_info = info
            return info
        except Exception as e:
            logger.warning("探测 GROMACS 版本失败: %s", e)
            self._cached_info = GromacsSoftwareInfo(
                found=False,
                executable_path=str(exe_path),
                probe_output=f"执行版本探测异常: {e}",
            )
            return self._cached_info

    def _find_executable(self) -> Path | None:
        """定位 GROMACS 二进制可执行文件"""
        # 1. 显式自定义路径
        if self.custom_path and self.custom_path.is_file() and (os.name != "nt" or self.custom_path.suffix.lower() == ".exe"):
            return self.custom_path

        # 2. PATH 环境变量中搜索常规二进制名
        binary_names = ["gmx", "gmx_mpi", "gmx_d", "gmx_cuda"]
        if os.name == "nt":
            binary_names = [f"{b}.exe" for b in binary_names] + binary_names

        for name in binary_names:
            found = shutil.which(name)
            if found and os.path.isfile(found) and (os.name != "nt" or Path(found).suffix.lower() == ".exe"):
                return Path(found).resolve()

        # 3. 常见操作系统默认安装路径搜索
        standard_locations: list[str] = []
        if os.name == "nt":
            standard_locations.extend([
                r"C:\Program Files\GROMACS\bin\gmx.exe",
                r"C:\gromacs\bin\gmx.exe",
                r"C:\Program Files (x86)\GROMACS\bin\gmx.exe",
            ])
        else:
            standard_locations.extend([
                "/usr/local/gromacs/bin/gmx",
                "/usr/bin/gmx",
                "/opt/gromacs/bin/gmx",
                "/usr/local/bin/gmx",
            ])

        for loc in standard_locations:
            p = Path(loc)
            if p.is_file():
                return p.resolve()

        return None

    def _parse_version_output(self, output: str, exe_path: str) -> GromacsSoftwareInfo:
        """从 gmx --version 输出中解析关键参数"""
        version_str = None
        precision = "unknown"
        mpi_enabled = False
        cuda_enabled = False
        simd = None

        # 提取版本号，例如: GROMACS version: 2023.3
        v_match = re.search(r"GROMACS\s+version:\s*([0-9a-zA-Z\.\-_]+)", output, re.IGNORECASE)
        if v_match:
            version_str = v_match.group(1)

        precision_match = re.search(r"Precision:\s*(\w+)", output, re.IGNORECASE)
        if precision_match:
            precision = precision_match.group(1)

        # GPU / CUDA 加速支持
        if re.search(r"GPU support:\s*CUDA", output, re.IGNORECASE):
            cuda_enabled = True

        # MPI 支持
        if re.search(r"(MPI library:\s*(OpenMPI|MPICH|Thread-MPI)|MPI enabled)", output, re.IGNORECASE):
            mpi_enabled = True

        # SIMD 指令集
        simd_match = re.search(r"SIMD instructions:\s*([^\r\n]+)", output, re.IGNORECASE)
        if simd_match:
            simd = simd_match.group(1).strip()

        return GromacsSoftwareInfo(
            found=True,
            executable_path=exe_path,
            version=version_str or "Unknown",
            precision=precision,
            mpi_enabled=mpi_enabled,
            cuda_enabled=cuda_enabled,
            simd=simd,
            probe_output=output[:1000],  # 截断保留前 1000 字符
        )

    def run_bounded(
        self,
        subcommand: str,
        arguments: list[str],
        timeout_seconds: int = 300,
        custom_cwd: Path | str | None = None,
        cancel_event=None,
    ) -> ExecutionResult:
        """
        受控有界执行 GROMACS 子命令：
        - 验证白名单
        - 校验入参字符
        - 严格设置 shell=False
        - 实施超时与输出截断
        """
        subcmd_clean = subcommand.strip().lower()
        if subcmd_clean not in ALLOWED_SUBCOMMANDS:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                error_message=f"拒绝执行未授权的 GROMACS 子命令 '{subcommand}'",
            )

        # 校验参数安全性
        for arg in arguments:
            if not isinstance(arg, str):
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    error_message="参数必须为字符串",
                )
            if FORBIDDEN_CHAR_REGEX.search(arg):
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    error_message=f"参数中含有危险非法字符: {arg}",
                )
            if PATH_TRAVERSAL_REGEX.search(arg):
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    error_message=f"参数中检测到越界路径穿越风险 (..): {arg}",
                )

        for arg in arguments:
            value = arg.replace("\\", "/")
            candidate = (self.workspace_dir / value).resolve()
            if ":" in value or value.startswith("/") or not candidate.is_relative_to(self.workspace_dir):
                return ExecutionResult(-1, "", "", 0.0, "Path outside workspace is forbidden")
        if custom_cwd and not Path(custom_cwd).resolve().is_relative_to(self.workspace_dir):
            return ExecutionResult(-1, "", "", 0.0, "Working directory outside workspace")

        info = self.probe()
        if not info.found or not info.executable_path:
            return ExecutionResult(
                exit_code=-2,
                stdout="",
                stderr=info.probe_output or "",
                duration_seconds=0.0,
                error_message="本节点未安装或未检测到可用 GROMACS 可执行文件",
            )

        # 确定工作目录（严格校验目录逃逸）
        work_dir = Path(custom_cwd).resolve() if custom_cwd else self.workspace_dir
        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)

        # 超时时间有界收敛 (最小 5 秒，不超过 max_timeout)
        bounded_timeout = max(5, min(timeout_seconds, self.max_timeout))

        # 构建子进程执行参数列表 (shell=False)
        cmd = [info.executable_path, "--version" if subcmd_clean == "version" else subcmd_clean, *arguments]
        if self._wsl:
            # Direct argv and --cd avoid shell interpolation of paths or parameters.
            cmd = [info.executable_path, "--cd", str(work_dir), "--exec", "gmx", *cmd[1:]]

        start_time = time.monotonic()
        try:
            from chemcompute.node.process import run_capped

            proc = run_capped(
                cmd,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=bounded_timeout,
                cancel_event=None if self._wsl else cancel_event,
                shell=False,
            )
            duration = round(time.monotonic() - start_time, 3)

            # 输出安全截断 (最大 500KB)
            max_bytes = 500_000
            stdout_str = proc.stdout
            if len(stdout_str) > max_bytes:
                stdout_str = stdout_str[:max_bytes] + "\n...[输出过长，已截断]"
            stderr_str = proc.stderr
            if len(stderr_str) > max_bytes:
                stderr_str = stderr_str[:max_bytes] + "\n...[输出过长，已截断]"

            return ExecutionResult(
                exit_code=proc.returncode,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            duration = round(time.monotonic() - start_time, 3)
            return ExecutionResult(
                exit_code=-9,
                stdout="",
                stderr="",
                duration_seconds=duration,
                error_message=f"作业运行超时，在 {bounded_timeout} 秒后被系统强制中断",
            )
        except Exception as e:
            duration = round(time.monotonic() - start_time, 3)
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=duration,
                error_message=f"执行子进程异常: {e}",
            )
