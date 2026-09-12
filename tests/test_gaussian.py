import os
import sys
import threading
from pathlib import Path
from unittest import mock

from chemcompute.node.adapters.gaussian import (
    NORMAL_TERMINATION_MARKER,
    GaussianAdapter,
)


class PythonStandinGaussianAdapter(GaussianAdapter):
    """用于测试的 Standin 适配器，通过覆盖 probe 返回本地 Python 解释器"""

    def probe(self) -> dict:
        return {
            "found": True,
            "executable_path": sys.executable,
            "version": "installed; not version-tested",
            "reason": None,
        }


# =================== 探测 (Probe) 测试 ===================

def test_probe_not_found(tmp_path):
    """当 PATH 中没有 g16/g09 且未指定自定义路径时，probe 应返回 found=False"""
    with mock.patch("shutil.which", return_value=None):
        adapter = GaussianAdapter(workspace_dir=tmp_path)
        info = adapter.probe()
        assert info["found"] is False
        assert info["executable_path"] is None
        assert info["version"] is None
        assert "not found" in info["reason"]


def test_probe_custom_valid_executable(tmp_path):
    """指定合法的自定义可执行文件路径应探测成功"""
    fake_g16 = tmp_path / ("g16.exe" if os.name == "nt" else "g16")
    fake_g16.write_text("binary", encoding="utf-8")

    adapter = GaussianAdapter(custom_executable_path=fake_g16, workspace_dir=tmp_path)
    info = adapter.probe()
    assert info["found"] is True
    assert Path(info["executable_path"]) == fake_g16.resolve()
    assert info["version"] == "installed; not version-tested"
    assert info["reason"] is None


def test_probe_custom_invalid_executable(tmp_path):
    """指定非法名称或不存在的可执行文件应探测失败"""
    # 名字不是 g16/g09
    bad_name = tmp_path / "other_tool.exe"
    bad_name.write_text("binary", encoding="utf-8")
    adapter = GaussianAdapter(custom_executable_path=bad_name, workspace_dir=tmp_path)
    info = adapter.probe()
    assert info["found"] is False

    # 文件不存在
    non_existent = tmp_path / "g16.exe"
    adapter2 = GaussianAdapter(custom_executable_path=non_existent, workspace_dir=tmp_path)
    info2 = adapter2.probe()
    assert info2["found"] is False


def test_probe_never_executes_binary(tmp_path):
    """Probe 探测过程严禁启动任何子进程执行"""
    fake_g16 = tmp_path / ("g16.exe" if os.name == "nt" else "g16")
    fake_g16.write_text("binary", encoding="utf-8")

    adapter = GaussianAdapter(custom_executable_path=fake_g16, workspace_dir=tmp_path)
    with mock.patch("subprocess.run") as mock_run, mock.patch("subprocess.Popen") as mock_popen:
        info = adapter.probe()
        assert info["found"] is True
        mock_run.assert_not_called()
        mock_popen.assert_not_called()


# =================== 输入参数与边界防御测试 ===================

def test_reject_unsupported_subcommand(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    res = adapter.run_bounded(subcommand="invalid_cmd", arguments=["job.gjf"])
    assert res.exit_code == -1
    assert "Unsupported subcommand" in res.error_message


def test_reject_invalid_arguments_count_and_types(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    # 无参数
    assert adapter.run_bounded("run", []).exit_code == -1
    # 多个参数
    assert adapter.run_bounded("run", ["a.gjf", "b.gjf"]).exit_code == -1
    # 非字符串参数
    assert adapter.run_bounded("run", [123]).exit_code == -1


def test_reject_invalid_extension(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    f = tmp_path / "job.txt"
    f.write_text("# test", encoding="utf-8")
    res = adapter.run_bounded("run", ["job.txt"])
    assert res.exit_code == -1
    assert ".gjf or .com" in res.error_message


def test_reject_dangerous_characters(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    for bad in ["job.gjf;rm -rf", "job.gjf|whoami", "job.gjf`ls`", "job.gjf$HOME"]:
        res = adapter.run_bounded("run", [bad])
        assert res.exit_code == -1
        assert "forbidden characters" in res.error_message


def test_reject_path_traversal_and_absolute_paths(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    for bad in ["../outside.gjf", "..\\outside.gjf", "sub/../../bad.gjf", "/etc/job.gjf", "C:\\data\\job.gjf"]:
        res = adapter.run_bounded("run", [bad])
        assert res.exit_code == -1


def test_reject_missing_input_file(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    res = adapter.run_bounded("run", ["nonexistent.gjf"])
    assert res.exit_code == -1
    assert "Input file not found" in res.error_message


# =================== 输入文件内容语法及安全性测试 ===================

def test_reject_input_exceeding_2mib(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    large_file = tmp_path / "huge.gjf"
    # 创建稍大于 2MiB 的文件
    large_file.write_bytes(b"A" * (2 * 1024 * 1024 + 10))
    res = adapter.run_bounded("run", ["huge.gjf"])
    assert res.exit_code == -1
    assert "exceeds 2MiB limit" in res.error_message


def test_reject_external_inclusion_directives(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)

    # @include 测试
    f1 = tmp_path / "test_inc1.gjf"
    f1.write_text("%mem=1GB\n@include other.gjf\n#p b3lyp\n", encoding="utf-8")
    assert adapter.run_bounded("run", ["test_inc1.gjf"]).exit_code == -1

    # @filename 形式测试
    f2 = tmp_path / "test_inc2.gjf"
    f2.write_text("%mem=1GB\n@/etc/passwd\n#p b3lyp\n", encoding="utf-8")
    assert adapter.run_bounded("run", ["test_inc2.gjf"]).exit_code == -1


def test_reject_forbidden_link0_directives(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)

    for directive in ["%kjob l9999", "%subst l502 /tmp/hack", "%lindaworkers=node1,node2"]:
        f = tmp_path / "test_dir.gjf"
        f.write_text(f"{directive}\n#p b3lyp\n", encoding="utf-8")
        res = adapter.run_bounded("run", ["test_dir.gjf"])
        assert res.exit_code == -1
        assert "Forbidden Link0 directive" in res.error_message


def test_reject_dangerous_link0_paths(tmp_path):
    adapter = GaussianAdapter(workspace_dir=tmp_path)

    # 包含绝对路径
    f_abs = tmp_path / "test_abs.gjf"
    f_abs.write_text("%chk=C:\\bad\\test.chk\n#p b3lyp\n", encoding="utf-8")
    assert adapter.run_bounded("run", ["test_abs.gjf"]).exit_code == -1

    # 包含相对路径穿越
    f_trav = tmp_path / "test_trav.gjf"
    f_trav.write_text("%oldchk=../secret.chk\n#p b3lyp\n", encoding="utf-8")
    assert adapter.run_bounded("run", ["test_trav.gjf"]).exit_code == -1

    # 包含多路径逗号切分
    f_comma = tmp_path / "test_comma.gjf"
    f_comma.write_text("%rwf=a.rwf,200MB,b.rwf,200MB\n#p b3lyp\n", encoding="utf-8")
    assert adapter.run_bounded("run", ["test_comma.gjf"]).exit_code == -1

    # 包含非法字符
    f_char = tmp_path / "test_char.gjf"
    f_char.write_text("%int=test;evil.int\n#p b3lyp\n", encoding="utf-8")
    assert adapter.run_bounded("run", ["test_char.gjf"]).exit_code == -1


def test_allow_safe_link0_and_scientific_directives(tmp_path):
    """允许安全的相对路径 %chk、%mem、%nprocshared 与 Route，不进行篡改"""
    adapter = GaussianAdapter(workspace_dir=tmp_path)
    f = tmp_path / "safe.gjf"
    content = (
        "%chk=subdir/molecule.chk\n"
        "%mem=4GB\n"
        "%nprocshared=4\n"
        "#p b3lyp/6-31g* opt freq\n\n"
        "Water molecule\n\n"
        "0 1\n"
        "O 0.0 0.0 0.0\n"
        "H 0.0 0.0 0.9\n"
        "H 0.0 0.9 0.0\n\n"
    )
    (tmp_path / "subdir").mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")

    input_file, err = adapter._validate_input(["safe.gjf"])
    assert err is None
    assert input_file == f.resolve()
    # 确认原始文件内容未被修改
    assert f.read_text(encoding="utf-8") == content

    with mock.patch.object(adapter, "probe", return_value={"found": True, "executable_path": "fake_g16.exe", "version": "installed; not version-tested", "reason": None}):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.poll.side_effect = [None, 0]
            mock_proc.returncode = 0
            mock_proc.stdout.read.side_effect = [f"{NORMAL_TERMINATION_MARKER}\n".encode(), b""]
            mock_proc.stderr.read.return_value = b""
            mock_proc.pid = 99999
            mock_popen.return_value = mock_proc

            res = adapter.run_bounded("run", ["safe.gjf"])
            assert res.exit_code == 0
            assert NORMAL_TERMINATION_MARKER in res.stdout


# =================== 运行控制、日志与终止状态测试 ===================

def test_run_success_with_normal_termination_marker(tmp_path):
    """当进程 exit 0 且日志尾部包含 Normal termination 时判定成功"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "calc.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import sys\n"
        "print('Starting SCF iteration')\n"
        f"print('{NORMAL_TERMINATION_MARKER} 16 at Fri Sep 11 12:00:00 2026.')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["calc.gjf"])
    assert res.exit_code == 0
    assert res.error_message is None
    assert NORMAL_TERMINATION_MARKER in res.stdout

    # 验证日志落盘: inputstem.log 与 inputstem.stderr.log
    log_file = tmp_path / "calc.log"
    stderr_file = tmp_path / "calc.stderr.log"
    assert log_file.exists()
    assert stderr_file.exists()
    assert NORMAL_TERMINATION_MARKER in log_file.read_text(encoding="utf-8")


def test_run_exit_zero_without_normal_termination_marker_is_failure(tmp_path):
    """即使进程 exit_code 为 0，缺少 Normal termination 必须判定为失败"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "incomplete.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import sys\n"
        "print('Calculation crashed without normal termination')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["incomplete.gjf"])
    assert res.exit_code == 1
    assert "Normal termination marker not found" in res.error_message


def test_run_process_exit_code_error(tmp_path):
    """进程返回非零退出码时，正确反映 exit_code 与错误信息"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "error.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import sys\n"
        "sys.stderr.write('Fatal error in l9999.exe\\n')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["error.gjf"])
    assert res.exit_code == 2
    assert "Fatal error in l9999.exe" in res.stderr


def test_run_cancellation_terminates_process(tmp_path):
    """测试 cancel_event 能够可靠触发 psutil 进程树中断"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "cancel.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import time\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )

    cancel_event = threading.Event()
    # 0.2 秒后触发取消
    threading.Timer(0.2, cancel_event.set).start()

    res = adapter.run_bounded("run", ["cancel.gjf"], timeout_seconds=30, cancel_event=cancel_event)
    assert res.exit_code == -9
    assert "cancelled" in res.error_message.lower()


def test_run_timeout_terminates_process(tmp_path):
    """测试超时能够自动触发进程中断"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "timeout.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import time\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["timeout.gjf"], timeout_seconds=0.3)
    assert res.exit_code == -9
    assert "timed out" in res.error_message.lower()


def test_gauss_scrdir_injected_for_child_only(tmp_path):
    """GAUSS_SCRDIR=workspace/scratch 仅为子进程注入，不污染父进程环境变量"""
    orig_env = os.environ.get("GAUSS_SCRDIR")

    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "env_check.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import os\n"
        "import sys\n"
        "print('CHILD_SCRDIR=' + os.environ.get('GAUSS_SCRDIR', 'NONE'))\n"
        f"print('{NORMAL_TERMINATION_MARKER}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["env_check.gjf"])
    assert res.exit_code == 0
    expected_scratch = str((tmp_path / "scratch").resolve())
    assert f"CHILD_SCRDIR={expected_scratch}" in res.stdout

    # 父进程环境变量保持不变
    assert os.environ.get("GAUSS_SCRDIR") == orig_env


def test_unix_stdin_mode_execution(tmp_path):
    """测试 Unix 平台下通过标准输入传递 input 文件的逻辑"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    adapter._is_windows = False  # 强制模拟 Unix 平台逻辑

    gjf = tmp_path / "unix_stdin.gjf"
    gjf.write_text(
        "# p b3lyp\n"
        "import sys\n"
        "print('Executed via stdin stream!')\n"
        f"print('{NORMAL_TERMINATION_MARKER}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["unix_stdin.gjf"])
    assert res.exit_code == 0
    assert "Executed via stdin stream!" in res.stdout


def test_output_and_disk_log_capping(tmp_path):
    """测试内存保留上限 (500KB) 与磁盘日志文件写入"""
    adapter = PythonStandinGaussianAdapter(workspace_dir=tmp_path)
    gjf = tmp_path / "large_output.gjf"
    # 生成超过 600KB 的输出
    gjf.write_text(
        "# p b3lyp\n"
        "import sys\n"
        "sys.stdout.write('A' * 600000)\n"
        "sys.stdout.write('\\n')\n"
        f"sys.stdout.write('{NORMAL_TERMINATION_MARKER}\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    res = adapter.run_bounded("run", ["large_output.gjf"])
    assert res.exit_code == 0
    # 内存保留截断为 <= 500,000 字节
    assert len(res.stdout) <= 500_000

    # 磁盘日志包含全部或硬截断内容
    log_file = tmp_path / "large_output.log"
    assert log_file.stat().st_size > 500_000
