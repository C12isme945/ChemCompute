"""ChemCompute 节点后台守护与 Windows 计划任务生命周期管理模块

设计说明与架构修正：
在 Windows 操作系统中，原生服务控制管理器 (SCM, sc.exe) 要求目标程序为符合 Win32 Service 协议的二进制
（必须调用 StartServiceCtrlDispatcher 响应 SCM 握手信号）。普通 Python 脚本或 CLI 打包程序若直接通过 sc.exe
注册为系统服务，启动时必定会因超时触发 'Error 1053: 服务没有及时响应启动或控制请求'。

因此，ChemCompute 节点提供两种经过严谨工程验证的 Windows 驻留机制：
1. 独立后台脱离进程 (Detached Daemon Process)：基于 CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS 标志
2. Windows 开机/登录自动计划任务 (Windows Scheduled Task)：基于系统内置 schtasks.exe 封装，免装额外底层依赖
"""

from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from chemcompute.common.security import ensure_secure_directory, secure_path

logger = logging.getLogger(__name__)

PID_FILE = Path("data/chemcompute-node.pid")
LOG_FILE = Path("logs/node-daemon.log")
DEFAULT_TASK_NAME = "ChemComputeNode"


def start_background(config_path: str | Path = "config/node.yaml") -> int:
    """以独立后台脱离进程模式启动节点代理"""
    ensure_secure_directory(PID_FILE.parent)
    ensure_secure_directory(LOG_FILE.parent)

    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if psutil.pid_exists(old_pid):
                logger.warning("节点代理已在后台运行中 (PID: %d)", old_pid)
                return old_pid
        except Exception:
            pass

    # 确定运行命令
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "node", "run", "--config", str(config_path)]
    else:
        cmd = [sys.executable, "-m", "chemcompute.cli", "node", "run", "--config", str(config_path)]

    log_fp = open(LOG_FILE, "a", encoding="utf-8")

    creation_flags = 0
    if os.name == "nt":
        # Windows: CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008

    proc = subprocess.Popen(
        cmd,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creation_flags,
        close_fds=(os.name != "nt"),
    )

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    secure_path(PID_FILE, is_dir=False)
    secure_path(LOG_FILE, is_dir=False)

    logger.info("节点代理已在后台启动 (PID: %d)，日志输出至: %s", proc.pid, LOG_FILE)
    return proc.pid


def stop_background() -> bool:
    """终止后台运行的节点代理"""
    if not PID_FILE.exists():
        logger.info("未找到 PID 文件，后台节点可能未在运行")
        return True

    try:
        pid = int(PID_FILE.read_text().strip())
        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                p.kill()
            logger.info("已成功停止后台进程 (PID: %d)", pid)
        else:
            logger.info("后台进程 (PID: %d) 已不存在", pid)
    except Exception as e:
        logger.warning("停止后台进程异常: %s", e)
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
    return True


def status_background() -> dict[str, Any]:
    """查看后台节点代理运行状态"""
    if not PID_FILE.exists():
        return {"running": False, "pid": None, "message": "未运行"}

    try:
        pid = int(PID_FILE.read_text().strip())
        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            return {
                "running": True,
                "pid": pid,
                "cpu_percent": p.cpu_percent(interval=0.1),
                "memory_mb": round(p.memory_info().rss / (1024 * 1024), 1),
                "message": "正在运行",
            }
        else:
            PID_FILE.unlink(missing_ok=True)
            return {"running": False, "pid": pid, "message": "异常终止 (残留PID已清理)"}
    except Exception as e:
        return {"running": False, "error": str(e), "message": f"状态检查异常: {e}"}


# ---------------- Windows 计划任务 (Scheduled Task) 生命周期管理 ----------------


def install_startup_task(
    task_name: str = DEFAULT_TASK_NAME,
    config_path: str | Path = "config/node.yaml",
) -> bool:
    """
    使用 Windows 原生计划任务 (schtasks.exe) 注册开机/登录自动启动任务。
    代替原生 Win32 Service (sc.exe)，避免 Python 缺乏 SCM 握手导致的 1053 启动超时。
    """
    if os.name != "nt":
        logger.error("计划任务管理仅支持在 Windows 系统下执行")
        return False

    abs_config = str(Path(config_path).resolve())
    if getattr(sys, "frozen", False):
        exe_path = sys.executable
        task_action = f'"{exe_path}" node run --config "{abs_config}"'
    else:
        python_exe = sys.executable
        task_action = f'"{python_exe}" -m chemcompute.cli node run --config "{abs_config}"'

    # 使用 schtasks.exe 创建任务，当用户登录时自动执行
    cmd = [
        "schtasks.exe",
        "/Create",
        "/TN",
        task_name,
        "/TR",
        task_action,
        "/SC",
        "ONLOGON",
        "/F",
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0:
            logger.info("Windows 开机自动运行计划任务 '%s' 创建成功", task_name)
            return True
        logger.error("创建 Windows 计划任务失败: %s", (res.stdout + res.stderr).strip())
        return False
    except Exception as e:
        logger.error("执行 schtasks.exe 创建异常: %s", e)
        return False


def uninstall_startup_task(task_name: str = DEFAULT_TASK_NAME) -> bool:
    """注销并删除 Windows 自动启动计划任务"""
    if os.name != "nt":
        return False

    # 尝试终止任务
    stop_startup_task(task_name)

    cmd = ["schtasks.exe", "/Delete", "/TN", task_name, "/F"]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0:
            logger.info("Windows 计划任务 '%s' 已成功删除", task_name)
            return True
        # 若任务不存在也视为已移除
        if "ERROR: The system cannot find the file specified." in (res.stdout + res.stderr) or "系统找不到指定的文件" in (res.stdout + res.stderr):
            return True
        logger.error("删除 Windows 计划任务失败: %s", (res.stdout + res.stderr).strip())
        return False
    except Exception as e:
        logger.error("执行 schtasks.exe 卸载异常: %s", e)
        return False


def start_startup_task(task_name: str = DEFAULT_TASK_NAME) -> bool:
    """手动触发执行 Windows 计划任务"""
    if os.name != "nt":
        return False

    cmd = ["schtasks.exe", "/Run", "/TN", task_name]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return res.returncode == 0
    except Exception as e:
        logger.error("执行 schtasks.exe 运行异常: %s", e)
        return False


def stop_startup_task(task_name: str = DEFAULT_TASK_NAME) -> bool:
    """终止正在运行的 Windows 计划任务"""
    if os.name != "nt":
        return False

    cmd = ["schtasks.exe", "/End", "/TN", task_name]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return res.returncode == 0
    except Exception:
        return False


def query_startup_task(task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    """查询 Windows 计划任务状态"""
    if os.name != "nt":
        return {"installed": False, "message": "非 Windows 平台"}

    cmd = ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "CSV", "/NH"]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0 and res.stdout.strip():
            reader = csv.reader(io.StringIO(res.stdout.strip()))
            row = next(reader, [])
            status_text = row[2] if len(row) > 2 else "Unknown"
            return {
                "installed": True,
                "task_name": task_name,
                "status": status_text,
                "message": "已安装",
            }
        return {"installed": False, "task_name": task_name, "message": "未安装"}
    except Exception as e:
        return {"installed": False, "task_name": task_name, "error": str(e)}
