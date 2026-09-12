"""ChemCompute Gaussian 安全适配器

安全约束与边界设计：
1. 可执行文件探测：仅探测 g16.exe/g09.exe (Windows) 或 g16/g09 (Unix)，不执行任何子进程命令
2. 输入文件受控与边界验证：
   - 严格限定仅接收单个位于 workspace 内的 .gjf / .com 相对路径文件
   - 输入文件大小限制 <= 2MiB
   - 严禁外部代码或文件包含指令 (@include, @, %kjob, %subst, %lindaworkers)
   - 限制 Link0 路径指令 (%chk, %oldchk, %rwf, %int, %d2e, %scr) 必须为 workspace 内的安全相对路径
   - 严禁多文件逗号切分、绝对路径、盘符与父级路径穿越 (..)
   - 不修改科学计算 Route 路线或内存/并行核数指令
3. 受控有界执行：
   - 绝不使用 shell=True，严防 Shell 注入
   - 仅为子进程注入 GAUSS_SCRDIR=workspace/scratch 环境变量
   - 捕获 stdout 到 inputstem.log，stderr 到 inputstem.stderr.log
   - 磁盘完整日志硬截断上限 10MiB，内存保留截断 500KB
   - 支持 Windows CLI 命令行入参与 Unix stdin 重定向
   - 借助 psutil 强行终止进程树以实现可靠的取消和超时熔断
   - 严格以日志尾部 Normal termination of Gaussian 判定成功 (即使 exit code 为 0)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

from chemcompute.node.adapters.gromacs import ExecutionResult

logger = logging.getLogger(__name__)

# 危险字符匹配：禁止管道、重定向、命令级联、空字符
FORBIDDEN_CHAR_REGEX = re.compile(r"[;&|`$<>\r\n\0]")

# 路径穿越防御正则：禁止引用父级目录
PATH_TRAVERSAL_REGEX = re.compile(r"(\.\.[/\\]|[/\\]\.\.|^\.\.$)")

# 允许的 Link0 路径指令白名单集合
LINK0_PATH_DIRECTIVES = {"chk", "oldchk", "rwf", "int", "d2e", "scr"}

# 严格禁止的外部代码 / 包含指令
FORBIDDEN_LINK0_DIRECTIVES_REGEX = re.compile(
    r"%\s*(?:kjob|subst|lindaworkers)\b",
    re.IGNORECASE,
)

# 外部文件引入指令 (@ 或 @include)
INCLUDE_DIRECTIVE_REGEX = re.compile(
    r"(?:@include\b|^\s*@)",
    re.IGNORECASE | re.MULTILINE,
)

# Link0 指令提取正则
LINK0_REGEX = re.compile(
    r"^\s*%\s*([a-zA-Z0-9]+)\s*=\s*([^\r\n]*)",
    re.MULTILINE,
)

# 正常完成标志符
NORMAL_TERMINATION_MARKER = "Normal termination of Gaussian"

# 资源上限
MAX_INPUT_SIZE_BYTES = 2 * 1024 * 1024    # 2 MiB
MAX_LOG_DISK_BYTES = 10 * 1024 * 1024     # 10 MiB
MAX_MEMORY_BUFFER_BYTES = 500_000         # 500 KB


def _terminate_tree(proc: subprocess.Popen) -> None:
    """借助 psutil 深度递归终止整个子进程树"""
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.Error):
        children = []

    for child in reversed(children):
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.Error):
            pass

    if proc.poll() is None:
        try:
            proc.kill()
        except (ProcessLookupError, psutil.Error):
            pass

    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _check_normal_termination(log_path: Path) -> bool:
    """检查日志文件末尾是否包含正常结束标识"""
    if not log_path.is_file():
        return False
    try:
        size = log_path.stat().st_size
        read_size = min(size, 65536)
        with open(log_path, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            tail = f.read().decode("utf-8", errors="replace")
        return NORMAL_TERMINATION_MARKER in tail
    except Exception:
        return False


class GaussianAdapter:
    """Gaussian 安全执行适配器"""

    def __init__(
        self,
        custom_executable_path: str | Path | None = None,
        workspace_dir: str | Path = "data/workspace",
        max_timeout_seconds: int = 3600,
    ) -> None:
        self.custom_path = Path(custom_executable_path).resolve() if custom_executable_path else None
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.max_timeout = max_timeout_seconds
        self._is_windows: bool | None = None  # 用于单元测试覆盖不同操作系统分支

    def probe(self) -> dict:
        """
        静态安全探测系统中安装的 Gaussian 可执行程序。
        绝不调用可执行程序（避免触发商业许可证验证或联网检查任务）。
        """
        exe_path = self._find_executable()
        if not exe_path:
            return {
                "found": False,
                "executable_path": None,
                "version": None,
                "reason": "Gaussian executable (g16/g09) not found on PATH or explicit path",
            }

        return {
            "found": True,
            "executable_path": str(exe_path),
            "version": "installed; not version-tested",
            "reason": None,
        }

    def _find_executable(self) -> Path | None:
        """定位本地 Gaussian 二进制可执行文件"""
        is_win = (self._is_windows if self._is_windows is not None else (os.name == "nt"))
        valid_names = {"g16.exe", "g09.exe"} if is_win else {"g16", "g09"}

        # 1. 优先校验显式指定的自定义可执行文件路径
        if self.custom_path:
            if self.custom_path.is_file() and self.custom_path.name.lower() in valid_names:
                return self.custom_path
            return None

        # 2. 遍历 PATH 环境变量
        binary_names = ["g16.exe", "g09.exe"] if is_win else ["g16", "g09"]
        for name in binary_names:
            found = shutil.which(name)
            if found:
                p = Path(found).resolve()
                if p.is_file() and p.name.lower() in valid_names:
                    return p

        if is_win:
            roots = [Path(os.environ.get('SystemDrive', 'C:') + '/'),
                     Path(os.environ.get('ProgramFiles', 'C:/Program Files')),
                     Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')),
                     Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs']
            for root in roots:
                for folder in ('G09W', 'G16W', 'Gaussian 09W', 'Gaussian 16W'):
                    for name in binary_names:
                        candidate = root / folder / name
                        if candidate.is_file():
                            return candidate.resolve()

        return None

    def _validate_input(self, arguments: list[str]) -> tuple[Path | None, str | None]:
        """严格校验输入参数与输入文件内容安全规范"""
        if not isinstance(arguments, list) or len(arguments) != 1:
            return None, "Arguments must contain exactly one input file path (.gjf or .com)"

        arg = arguments[0]
        if not isinstance(arg, str):
            return None, "Argument must be a string"

        if FORBIDDEN_CHAR_REGEX.search(arg):
            return None, f"Argument contains forbidden characters: {arg}"

        if ".." in arg or PATH_TRAVERSAL_REGEX.search(arg):
            return None, f"Path traversal (..) not allowed in argument: {arg}"

        # 路径必须为相对路径且无盘符前缀
        clean_arg = arg.replace("\\", "/")
        if ":" in clean_arg or clean_arg.startswith("/"):
            return None, "Input path must be relative inside workspace"

        # 校验后缀名
        ext = Path(clean_arg).suffix.lower()
        if ext not in {".gjf", ".com"}:
            return None, f"Input file must have .gjf or .com extension: {arg}"

        input_file = (self.workspace_dir / clean_arg).resolve()
        if not input_file.is_relative_to(self.workspace_dir):
            return None, "Input path resolves outside workspace"

        if not input_file.is_file():
            return None, f"Input file not found: {arg}"

        # 校验文件大小上限 (<= 2MiB)
        file_size = input_file.stat().st_size
        if file_size > MAX_INPUT_SIZE_BYTES:
            return None, f"Input file exceeds 2MiB limit ({file_size} bytes)"

        # 读取内容进行语法安全扫描
        try:
            content = input_file.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return None, f"Failed to read input file: {e}"

        if re.search(r'\bexternal\s*[=(]', content, re.IGNORECASE):
            return None, 'External program route is forbidden'
        # 检查是否包含 @include 或外部文件引入指令
        if INCLUDE_DIRECTIVE_REGEX.search(content):
            return None, "External file inclusion directive (@ or @include) is forbidden"

        # 检查是否包含 %kjob, %subst, %lindaworkers 等外部代码/进程调度指令
        if FORBIDDEN_LINK0_DIRECTIVES_REGEX.search(content):
            return None, "Forbidden Link0 directive in input (%kjob, %subst, or %lindaworkers)"

        # 严格校验 Link0 文件路径指令 (%chk, %oldchk, %rwf, %int, %d2e, %scr)
        for match in LINK0_REGEX.finditer(content):
            key = match.group(1).lower()
            val = match.group(2).strip().strip("\"'")

            if key in LINK0_PATH_DIRECTIVES:
                if not val:
                    return None, f"Empty path in %{key} directive"
                if "," in val:
                    return None, f"Embedded comma multiple paths not allowed in %{key} directive: {val}"
                if ".." in val or PATH_TRAVERSAL_REGEX.search(val):
                    return None, f"Path traversal (..) not allowed in %{key} directive: {val}"
                val_clean = val.replace("\\", "/")
                if ":" in val_clean or val_clean.startswith("/"):
                    return None, f"Absolute path not allowed in %{key} directive: {val}"
                if FORBIDDEN_CHAR_REGEX.search(val):
                    return None, f"Forbidden characters in %{key} directive: {val}"

                resolved_path = (self.workspace_dir / val_clean).resolve()
                if not resolved_path.is_relative_to(self.workspace_dir):
                    return None, f"Path in %{key} directive resolves outside workspace: {val}"

        return input_file, None

    def run_bounded(
        self,
        subcommand: str = "run",
        arguments: list[str] | None = None,
        timeout_seconds: int = 300,
        cancel_event: threading.Event | None = None,
        process_record=None,
    ) -> ExecutionResult:
        """受控有界执行 Gaussian 计算任务"""
        subcmd_clean = (subcommand or "").strip().lower()
        if subcmd_clean != "run":
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                error_message=f"Unsupported subcommand '{subcommand}'. Only 'run' is supported.",
            )

        args_list = arguments if arguments is not None else []
        input_file, validation_error = self._validate_input(args_list)
        if validation_error or not input_file:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                error_message=validation_error or "Input validation failed",
            )

        info = self.probe()
        if not info.get("found") or not info.get("executable_path"):
            return ExecutionResult(
                exit_code=-2,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                error_message=f"Gaussian executable not found: {info.get('reason')}",
            )

        exe_path = Path(info["executable_path"])

        # 仅为子进程注入 GAUSS_SCRDIR=workspace/scratch
        scratch_dir = (self.workspace_dir / "scratch").resolve()
        scratch_dir.mkdir(parents=True, exist_ok=True)
        child_env = os.environ.copy()
        child_env["GAUSS_SCRDIR"] = str(scratch_dir)

        # 准备日志输出文件路径: inputstem.log 与 inputstem.stderr.log
        stem = input_file.stem
        log_path = input_file.parent / f"{stem}.log"
        stderr_log_path = input_file.parent / f"{stem}.stderr.log"
        if log_path.exists():
            import uuid
            log_path.rename(log_path.with_name(stem + ".previous-" + uuid.uuid4().hex + ".log"))

        # 判断操作系统平台及 CLI / stdin 调用规范
        is_win = (self._is_windows if self._is_windows is not None else (os.name == "nt"))
        rel_input = input_file.relative_to(self.workspace_dir)

        stdin_file = None
        if is_win:
            # Windows Gaussian CLI: 直接接收输入文件名
            cmd = [str(exe_path), str(rel_input)]
            stdin_target = subprocess.DEVNULL
        else:
            # Unix Gaussian: 通过标准输入重定向读取文件
            cmd = [str(exe_path)]
            try:
                stdin_file = open(input_file, "rb")
                stdin_target = stdin_file
            except Exception as e:
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    error_message=f"Failed to open input file for stdin: {e}",
                )

        # 超时时间有界收敛
        bounded_timeout = max(0.1, min(float(timeout_seconds), float(self.max_timeout)))

        stdout_mem = bytearray()
        stderr_mem = bytearray()

        def _drain_and_stream(pipe, mem_target: bytearray, file_path: Path) -> None:
            bytes_written = 0
            try:
                with open(file_path, "wb") as f:
                    while True:
                        chunk = pipe.read(8192)
                        if not chunk:
                            break
                        # 磁盘输出实施硬截断上限
                        if bytes_written < MAX_LOG_DISK_BYTES:
                            avail_disk = MAX_LOG_DISK_BYTES - bytes_written
                            to_write = chunk[:avail_disk]
                            f.write(to_write)
                            bytes_written += len(to_write)
                            if bytes_written >= MAX_LOG_DISK_BYTES:
                                f.write(b"\n...[Log file capped at 10MiB]\n")
                                f.flush()
                        # 内存保留实施有界截断上限
                        if len(mem_target) < MAX_MEMORY_BUFFER_BYTES:
                            avail_mem = MAX_MEMORY_BUFFER_BYTES - len(mem_target)
                            mem_target.extend(chunk[:avail_mem])
            except Exception as e:
                logger.warning("Error streaming output to %s: %s", file_path, e)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        def save_process(value):
            if process_record:
                path = Path(process_record)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(value), encoding='utf-8')
                temporary.replace(path)

        start_time = time.monotonic()
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.workspace_dir),
                stdin=stdin_target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            if process_record:
                process = psutil.Process(proc.pid)
                save_process({'pid': proc.pid, 'created': process.create_time(), 'exe': process.exe(), 'argv': process.cmdline(), 'finished': False})
            # 启动双通道异步管道读取
            t_stdout = threading.Thread(
                target=_drain_and_stream,
                args=(proc.stdout, stdout_mem, input_file.with_suffix('.stdout.log') if is_win else log_path),
                daemon=True,
            )
            t_stderr = threading.Thread(
                target=_drain_and_stream,
                args=(proc.stderr, stderr_mem, stderr_log_path),
                daemon=True,
            )
            t_stdout.start()
            t_stderr.start()

            deadline = start_time + bounded_timeout
            while proc.poll() is None:
                if cancel_event and cancel_event.is_set():
                    _terminate_tree(proc)
                    t_stdout.join(timeout=2)
                    t_stderr.join(timeout=2)
                    duration = round(time.monotonic() - start_time, 3)
                    return ExecutionResult(
                        exit_code=-9,
                        stdout=stdout_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace"),
                        stderr=stderr_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace"),
                        duration_seconds=duration,
                        error_message="Computation cancelled",
                    )
                if time.monotonic() >= deadline:
                    _terminate_tree(proc)
                    t_stdout.join(timeout=2)
                    t_stderr.join(timeout=2)
                    duration = round(time.monotonic() - start_time, 3)
                    return ExecutionResult(
                        exit_code=-9,
                        stdout=stdout_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace"),
                        stderr=stderr_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace"),
                        duration_seconds=duration,
                        error_message=f"Computation timed out after {bounded_timeout} seconds",
                    )
                time.sleep(0.05)

            t_stdout.join(timeout=2)
            t_stderr.join(timeout=2)
            duration = round(time.monotonic() - start_time, 3)

            stdout_str = stdout_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace")
            stderr_str = stderr_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace")

            if is_win and not log_path.exists() and input_file.with_suffix('.stdout.log').exists():
                shutil.copyfile(input_file.with_suffix('.stdout.log'), log_path)
            if proc.returncode == 0:
                # 严格判定尾部 Normal termination 标记
                if _check_normal_termination(log_path) or (is_win and _check_normal_termination(input_file.with_suffix('.stdout.log'))):
                    return ExecutionResult(
                        exit_code=0,
                        stdout=stdout_str,
                        stderr=stderr_str,
                        duration_seconds=duration,
                        error_message=None,
                    )
                else:
                    return ExecutionResult(
                        exit_code=1,
                        stdout=stdout_str,
                        stderr=stderr_str,
                        duration_seconds=duration,
                        error_message="Normal termination marker not found in Gaussian log",
                    )
            else:
                return ExecutionResult(
                    exit_code=proc.returncode,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    duration_seconds=duration,
                    error_message=f"Gaussian process failed with exit code {proc.returncode}",
                )

        except Exception as e:
            if proc and proc.poll() is None:
                _terminate_tree(proc)
            duration = round(time.monotonic() - start_time, 3)
            return ExecutionResult(
                exit_code=-1,
                stdout=stdout_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace"),
                stderr=stderr_mem[:MAX_MEMORY_BUFFER_BYTES].decode("utf-8", errors="replace"),
                duration_seconds=duration,
                error_message=f"Subprocess execution error: {e}",
            )
        finally:
            if process_record and proc and proc.poll() is not None:
                save_process({'finished': True, 'returncode': proc.returncode})
            if stdin_file:
                try:
                    stdin_file.close()
                except Exception:
                    pass
