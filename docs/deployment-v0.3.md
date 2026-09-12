# 0.3 部署、恢复与 Gaussian

## 当前主控

本机默认为 Controller + Compute。远程地址 https://chemcompute.666945726.xyz；来源为本机环回监听端口 8000，经单独的 Cloudflare Tunnel 转发。根域名及其他服务保持不变。隧道凭据只保存在本机运行目录，不随源码/安装包分发。远程节点需持有单次邀请码，桌面管理员操作需管理员密钥。

新安装的其他电脑选择 Compute node 并填写主控域名 URL；不要让每台电脑都选择 Both。主控程序和隧道在当前用户登录后启动，关闭桌面窗口继续后台运行；主控关机、睡眠、注销或离线时远程入口可能不可用。这不是 24 小时云服务器或 Windows 系统服务。

## 默认勾选依赖

WSL / Ubuntu、GROMACS、Display 驱动三项默认选中，可独立取消。下载与系统修改发生在安装完成后，可在桌面依赖页刷新状态或继续。需要 UAC 管理员授权。重启只提示并设置登录后继续，不强制重启。发行版不是 Ubuntu/Debian、缺少虚拟化、组织策略限制 Windows Update 或网络失败时，显示 needs_attention 和日志，不能宣称已安装。

GROMACS 通过 apt-get 安装发行版包；已有 gmx 验证成功则跳过。驱动使用 Windows Update Agent 查找硬件匹配的 Display 更新；没有更新时报告 no_applicable_update，不代表 CUDA 可用。不安装 Linux NVIDIA 显示驱动，不静默接受/安装 Gaussian。CPU 版 GROMACS 无法仅靠更新驱动变为 GPU 版。

## 失联与恢复

控制端以 SQLite 事务原子领取任务；每次派发带随机租约令牌。过期后标记 recovering，保留节点所有权，不会仅因网络超时重复发往其他节点。重连时验证节点认证和同一次派发令牌，然后恢复运行状态。

节点在每步前后保存 worker-state.json。控制端断网时继续本地当前计算；恢复连接后补传状态与结果，已完成步骤不重复运行。代理进程意外重启时检查原进程 PID、创建时间、程序和参数；WSL 还按 Linux 工作目录检查 gmx/timeout。原进程仍在运行则等待。

GROMACS mdrun 有有效检查点时追加 -cpi 并保留原科学参数。无检查点或中断的非 MD 步骤不冒险自动重跑。Gaussian 中断要求人工检查 chk/log 并提供明确续算输入；网络断开但计算正常完成的 Gaussian 结果可以自动补传。计算期限包含断网停顿时间。跨节点永久故障接管、远端实时检查点镜像和云高可用尚未实现。

## Gaussian

在节点与邀请码页填写本机已授权 g16.exe/g09.exe 路径，停止后台后保存并重启。任务选 gaussian，上传 ZIP，支持多步顺序执行 gjf/com。子进程 scratch 限定在任务目录，拒绝外部 Link0 路径、包含文件和外部执行指令，不修改方法/基组/内存/核数。节点探测是静态检查，不会借此调用商业程序。内存保留日志约 500 KB，管道日志约 10 MiB，结果包仍受约 100 MiB 限制。

已验证适配器的模拟执行、取消、错误退出、正常结束标志及输入边界；当前没有发现已授权 Gaussian，因此没有真实 Gaussian 科学计算实测。ZIP 中附带的已有 log 不会被当成本次成功标志。

## 验证命令

运行 pytest 和 Windows 构建/安装测试。安装了 GROMACS 的测试机器可运行 `python scripts/smoke_reconnect.py --exe dist/ChemCompute/ChemCompute.exe`，实际停止隔离主控与代理再重启，从真实检查点继续。`scripts/dependencies.ps1 -WSL -Gromacs -Drivers -PlanOnly` 只输出计划，不安装系统组件。

官方资料：[WSL 安装](https://learn.microsoft.com/en-us/windows/wsl/install)、[Windows Update API](https://learn.microsoft.com/en-us/windows/win32/wua_sdk/searching--downloading--and-installing-specific-updates)、[NVIDIA WSL 驱动](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)、[GROMACS 续算](https://manual.gromacs.org/2025.2/user-guide/managing-simulations.html)。
