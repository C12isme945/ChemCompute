# ChemCompute

将 Windows 电脑接入私人化学计算网络的开源 MVP。包含 Inno Setup 安装器、FastAPI/SQLite 控制端、中文 Web Console、后台节点代理和 GROMACS 适配器。MIT 许可。

**这是 0.1.0 预发布版。** 安装包不包含 GROMACS、COMSOL、ORCA、CUDA 或软件许可证。已验证 WSL GROMACS 两水分子 100 步 CPU 冒烟计算；不代表科学模型有效性。跨机吞吐量和无人登录开机运行尚未验证。

## 安装与第一次运行

1. 从 GitHub Releases 下载 `ChemCompute-Setup.exe`，对照 `SHA256SUMS.txt` 校验。安装包尚未代码签名。
2. 双击安装，选择 Controller、Compute node 或 Both。首次尝试选择 Both，即可在本机自动注册节点。
3. Controller 默认监听 `127.0.0.1:8000`。若其他电脑需要加入，请填本机 Tailscale IP，并在节点上填对应 URL 与控制台生成的单次邀请码。
4. 完成后选择 Start ChemCompute。打开开始菜单中的 Web Console；管理员密钥在 `%LOCALAPPDATA%\ChemComputeData\data\chemcompute-admin.secret`，用记事本打开后粘贴到登录框。
5. 在控制台查看节点 CPU、RAM、GPU 与软件探测，生成/撤销邀请码，创建 GROMACS 作业并查看状态和日志。

默认安装至当前用户目录，不要求管理员权限。可选“登录时启动”使用当前用户启动项；**不是原生 Windows Service，不保证注销后或登录前运行**。安装包不自动修改防火墙、SSH 或 Tailscale 登录。

配置、数据库、日志保存在 `%LOCALAPPDATA%\ChemComputeData`，升级保留已有配置。卸载删除程序与启动项，保留数据以便恢复。更换角色或主控地址需编辑 `config/*.yaml` 后重启程序。停止后台程序可在任务管理器结束 ChemCompute.exe；升级/卸载前先停止。

## 网络部署

主控和节点加入同一个受控 Tailscale 网络；先完成各自登录，然后将主控监听 IP 改为其 Tailscale IP。此 MVP 的 HTTP 不提供 TLS，**只用于本机或加密的 Tailscale 链路**，其他网络必须另行配置 HTTPS 反向代理。限制 tailnet ACL；不要映射到公网。

可选脚本（仅检测时不修改系统）：

```powershell
.\scripts\network.ps1
.\scripts\network.ps1 -InstallTailscale
.\scripts\network.ps1 -ConnectTailscale
# 仅在确实需要 SSH 时，以管理员 PowerShell 运行：
.\scripts\network.ps1 -EnableOpenSSH
```

SSH 并不是代理工作的前提。脚本将新建 SSH 规则限制到 Tailscale IPv4 地址段；仍需用 Tailscale ACL 和 SSH 账号权限限制访问。

## 源码运行

需要 Python 3.12。以下命令在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m chemcompute.cli setup --role both
.\.venv\Scripts\python -m chemcompute.cli controller run
# 另一终端生成邀请码，在节点配置填入邀请码后运行：
.\.venv\Scripts\python -m chemcompute.cli controller invite create
.\.venv\Scripts\python -m chemcompute.cli node run
```

安装版运行时位置也可通过 `CHEMCOMPUTE_HOME` 指定（用于测试/隔离实例）。示例配置在 `examples/`，不要把实际邀请码或令牌提交到 Git。

## GROMACS 范围

检测 PATH 或配置中的原生 `gromacs_custom_path`，无原生 EXE 时检测默认 WSL 发行版中的 GROMACS，执行真实 `gmx --version`。WSL 使用独立参数调用，不拼接 bash 命令。支持受限的 `check/grompp/mdrun/editconf/solvate/genion/energy` 子命令，传递参数列表，禁止 shell、目录逃逸，限制超时并保留最多 500 KB stdout/stderr。命令 `version` 映射到 `gmx --version`。

输入文件需要提前放到节点 `data/workspace`，在控制台参数中使用相对路径。**没有自动输入上传、结果文件下载、MPI 跨机并行、任务断点恢复或可靠重试。** 每节点同步执行作业，执行期间心跳会暂停，可能暂时显示离线；当前适用于短任务验证。不要用来调度关键长任务。工作目录不是操作系统沙箱，只处理可信科学输入。

## 构建和验证

```powershell
python -m pip install -r requirements-dev.txt
ruff check .
python -m pytest -q
# 安装官方 Inno Setup 6，然后：
.\scripts\build.ps1 -Python python -ISCC 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
python scripts\smoke_exe.py dist\ChemCompute\ChemCompute.exe
```

输出：`dist/ChemCompute-Setup.exe` 和 `dist/SHA256SUMS.txt`。构建步骤可重复执行，但不承诺二进制逐字节相同。GitHub Actions 会在 Linux/Windows 测试，在 Windows 打包并运行 EXE 端到端检查；推送 `v*` 标签会发布预发行版。详见 [架构](docs/architecture.md)、[发布说明](docs/releasing.md) 与 [安全边界](SECURITY.md)。

官方参考：[Inno Setup 编译器](https://jrsoftware.org/ishelp/topic_compilercmdline.htm)、[Tailscale 无人值守配置](https://tailscale.com/docs/how-to/run-unattended)。后者只影响联网工具，不会把 ChemCompute 变成系统服务。
