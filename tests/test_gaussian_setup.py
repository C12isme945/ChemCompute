from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

from chemcompute.common.config import NodeConfig, load_yaml_config
from chemcompute.gaussian_setup import (
    configure_node,
    inspect_installation,
    launch_installer,
)

# ===================== inspect_installation 测试 =====================


def test_inspect_installation_valid_g16_and_companions(tmp_path: Path):
    """测试合法 g16.exe 及其同目录下部分辅助工具的探测与返回"""
    install_dir = tmp_path / "Gaussian16"
    install_dir.mkdir()

    g16 = install_dir / "g16.exe"
    g16.write_text("dummy-binary", encoding="utf-8")
    (install_dir / "formchk.exe").write_text("dummy", encoding="utf-8")
    (install_dir / "gview.exe").write_text("dummy", encoding="utf-8")

    with mock.patch("subprocess.run") as mock_run, mock.patch("subprocess.Popen") as mock_popen:
        info = inspect_installation(g16)
        # 严格确保没有任何子进程或二进制被调用执行
        mock_run.assert_not_called()
        mock_popen.assert_not_called()

    assert info["valid"] is True
    assert info["executable"] == str(g16.resolve())
    assert info["directory"] == str(install_dir.resolve())
    assert info["tools"]["formchk.exe"] is True
    assert info["tools"]["gview.exe"] is True
    assert info["tools"]["g09w.exe"] is False
    assert info["tools"]["cubegen.exe"] is False
    assert info["probe"]["found"] is True


def test_inspect_installation_valid_g09(tmp_path: Path):
    """测试合法 g09.exe 探测"""
    g09 = tmp_path / "g09.exe"
    g09.write_text("dummy", encoding="utf-8")

    info = inspect_installation(g09)
    assert info["valid"] is True
    assert info["executable"] == str(g09.resolve())
    assert info["name"].lower() == "g09.exe"


def test_inspect_installation_invalidpath_nonexistent(tmp_path: Path):
    """invalidpath: 路径不存在应抛出 FileNotFoundError"""
    bad_path = tmp_path / "nonexistent" / "g16.exe"
    with pytest.raises(FileNotFoundError, match="不存在"):
        inspect_installation(bad_path)


def test_inspect_installation_invalidpath_is_directory(tmp_path: Path):
    """invalidpath: 传入目录路径应抛出 ValueError"""
    d = tmp_path / "g16.exe"
    d.mkdir()
    with pytest.raises(ValueError, match="不是有效文件"):
        inspect_installation(d)


def test_inspect_installation_invalidpath_bad_filename(tmp_path: Path):
    """invalidpath: 传入非 g09.exe/g16.exe 文件名应抛出 ValueError"""
    bad_exe = tmp_path / "notepad.exe"
    bad_exe.write_text("dummy", encoding="utf-8")
    with pytest.raises(ValueError, match="非法的 Gaussian 可执行文件名"):
        inspect_installation(bad_exe)


# ===================== launch_installer 测试 =====================


def test_launch_installer_licenseconsent_required(tmp_path: Path):
    """licenseconsent: 未显式提供 licensed=True 时必须拒绝启动"""
    installer = tmp_path / "setup.exe"
    installer.write_text("dummy", encoding="utf-8")

    with pytest.raises(ValueError, match="licensed=True"):
        launch_installer(installer, licensed=False)

    with pytest.raises(ValueError, match="licensed=True"):
        launch_installer(installer, licensed=None)  # type: ignore


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer launch contract")
def test_launch_installer_invalidpath_nonexistent(tmp_path: Path):
    """invalidpath: 安装文件不存在应抛出 FileNotFoundError"""
    bad_installer = tmp_path / "missing_setup.exe"
    with pytest.raises(FileNotFoundError, match="未找到"):
        launch_installer(bad_installer, licensed=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer launch contract")
def test_launch_installer_invalidpath_unsupported_extension(tmp_path: Path):
    """invalidpath: 不支持的文件类型 (.bat, .sh 等) 应抛出 ValueError"""
    bad_ext = tmp_path / "setup.bat"
    bad_ext.write_text("echo hi", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持的安装程序文件类型"):
        launch_installer(bad_ext, licensed=True)


def test_launch_installer_platform_check(tmp_path: Path):
    """测试非 Windows 操作系统下拒绝执行安装向导"""
    installer = tmp_path / "setup.exe"
    installer.write_text("dummy", encoding="utf-8")

    with mock.patch("sys.platform", "linux"):
        with pytest.raises(RuntimeError, match="仅支持在 Windows"):
            launch_installer(installer, licensed=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer launch contract")
def test_launch_installer_exe_interactive_mocked(tmp_path: Path):
    """测试 .exe 安装程序交互式启动 (无静默参数猜测，shell=False)"""
    installer = tmp_path / "Gaussian16_Setup.exe"
    installer.write_text("dummy", encoding="utf-8")

    mock_proc = mock.MagicMock(spec=subprocess.Popen)
    with mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        proc = launch_installer(installer, licensed=True)
        assert proc is mock_proc

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd == [str(installer.resolve())]
        assert kwargs.get("shell") is False
        assert kwargs.get("cwd") == str(installer.parent.resolve())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer launch contract")
def test_launch_installer_msi_interactive_mocked(tmp_path: Path):
    """测试 .msi 安装程序通过受信任绝对路径 msiexec /i 启动 (无静默参数)"""
    installer = tmp_path / "Gaussian16.msi"
    installer.write_text("dummy", encoding="utf-8")

    mock_proc = mock.MagicMock(spec=subprocess.Popen)
    with mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
         mock.patch("pathlib.Path.is_file", autospec=True, return_value=True):
        proc = launch_installer(installer, licensed=True)
        assert proc is mock_proc

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert len(cmd) == 3
        assert cmd[0].lower().endswith("msiexec.exe")
        assert cmd[1] == "/i"
        assert cmd[2] == str(installer.resolve())
        assert kwargs.get("shell") is False
        assert kwargs.get("cwd") == str(installer.parent.resolve())


# ===================== configure_node 测试 =====================


def test_configure_node_reject_while_desktop_backend_running(tmp_path: Path):
    """当 desktop_backend 存在活跃运行进程时，拒绝写入配置"""
    fake_g16 = tmp_path / "g16.exe"
    fake_g16.write_text("dummy", encoding="utf-8")

    mock_process = mock.MagicMock()
    with mock.patch("chemcompute.desktop_backend.running_process", return_value=mock_process):
        with pytest.raises(RuntimeError, match="桌面后台服务正在运行"):
            configure_node(fake_g16, config_path=tmp_path / "node.yaml")


def test_configure_node_invalidpath_executable(tmp_path: Path):
    """invalidpath: 传入不存在的 Gaussian 可执行程序应拒绝配置且不写入文件"""
    config_file = tmp_path / "node.yaml"
    bad_exe = tmp_path / "does_not_exist_g16.exe"

    with mock.patch("chemcompute.desktop_backend.running_process", return_value=None):
        with pytest.raises(FileNotFoundError):
            configure_node(bad_exe, config_path=config_file)

    assert not config_file.exists()


def test_configure_node_preservingconfig(tmp_path: Path):
    """preservingconfig: 仅更新 gaussian_custom_path 并完整保留既有字段"""
    fake_g16 = tmp_path / "bin" / "g16.exe"
    fake_g16.parent.mkdir()
    fake_g16.write_text("dummy", encoding="utf-8")

    config_file = tmp_path / "config" / "node.yaml"
    config_file.parent.mkdir()

    initial_data = {
        "controller_url": "http://10.20.30.40:8000",
        "node_name": "worker-gpu-01",
        "node_id": "node-uuid-123456",
        "node_token": "token-secret-abcdef",
        "invite_code": "cc-inv-test1234",
        "heartbeat_interval_seconds": 25,
        "gromacs_custom_path": "C:\\Tools\\Gromacs\\bin\\gmx.exe",
        "gaussian_custom_path": None,
        "workspace_dir": "D:\\ChemData\\workspace",
        "max_job_timeout_seconds": 7200,
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_data, f)

    with mock.patch("chemcompute.desktop_backend.running_process", return_value=None):
        cfg = configure_node(fake_g16, config_path=config_file)

    assert cfg.gaussian_custom_path == str(fake_g16.resolve())
    assert cfg.node_name == "worker-gpu-01"
    assert cfg.controller_url == "http://10.20.30.40:8000"
    assert cfg.node_id == "node-uuid-123456"
    assert cfg.node_token == "token-secret-abcdef"
    assert cfg.invite_code == "cc-inv-test1234"
    assert cfg.heartbeat_interval_seconds == 25
    assert cfg.gromacs_custom_path == "C:\\Tools\\Gromacs\\bin\\gmx.exe"
    assert cfg.workspace_dir == "D:\\ChemData\\workspace"
    assert cfg.max_job_timeout_seconds == 7200

    # 重新从磁盘读取 YAML 文件核对
    reloaded = load_yaml_config(config_file, NodeConfig)
    assert reloaded.gaussian_custom_path == str(fake_g16.resolve())
    assert reloaded.node_name == "worker-gpu-01"
    assert reloaded.controller_url == "http://10.20.30.40:8000"
    assert reloaded.node_id == "node-uuid-123456"
    assert reloaded.node_token == "token-secret-abcdef"
    assert reloaded.invite_code == "cc-inv-test1234"
    assert reloaded.heartbeat_interval_seconds == 25
    assert reloaded.gromacs_custom_path == "C:\\Tools\\Gromacs\\bin\\gmx.exe"
    assert reloaded.workspace_dir == "D:\\ChemData\\workspace"
    assert reloaded.max_job_timeout_seconds == 7200
