import os
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def show_roles_guide():
    print("""
================================================================================
                     ChemCompute 角色与权限体系详细说明
================================================================================

1. 👑 主控管理员 (Controller Admin):
   - 谁来运行：实验室课题组长、管理员、或拥有一台固定 IP / 长期开机电脑的负责人。
   - 职责：启动主控调度服务，管理算力节点，分配【管理员密钥 / 邀请码】，管理任务队列与灰度升级。
   - 访问地址：启动后默认访问 http://127.0.0.1:8000 (或局域网 IP)。

2. 💻 普通计算节点 (Worker Node):
   - 谁来运行：实验室组员电脑、宿舍高性能 PC、或插有 RTX 显卡的计算服务器。
   - 职责：安装运行 Agent，填入管理员提供的【管理员密钥】，后台默默执行 GROMACS/ORCA 等计算任务。
   - 权限隔离：普通节点无权修改主控配置，无权获取其他节点数据，计算任务完成后自动回收环境。

3. 🔬 科研作业提交者 (Submitter / 终端用户):
   - 谁来运行：平时需要进行分子动力学 (MD) 或量子化学 (DFT) 模拟的研究生与科研人员。
   - 职责：无需在自己电脑上安装复杂的 GROMACS 或 GPU 驱动，只需通过浏览器打开主控 Web 网址，
     上传分子结构打包包 (.zip)，即可利用整个集群的空闲算力并行计算，并实时下载轨迹与能量曲线。
================================================================================
""")


def main():
    print("================================================================================")
    print("          🧪 ChemCompute 分布式化学计算系统 v0.3.0")
    print("================================================================================")
    print("  请选择运行模式 / 您的角色：\n")
    print("  [1] 👑 管理员模式 (Admin)      - 启动主控调度中心与 Web 控制台")
    print("  [2] 💻 普通计算节点 (Worker)   - 接入已有计算集群贡献 CPU/GPU 算力")
    print("  [3] 🔬 科研作业提交 (Submitter) - 打开浏览器 Web 界面提交计算作业")
    print("  [4] 📖 查看角色权限与使用指南")
    print("  [0] 退出")
    print("================================================================================")

    choice = input("  请输入序号 [1/2/3/4/0] (直接回车默认 1): ").strip()
    if not choice:
        choice = "1"

    if choice == "1":
        from scripts.start_controller import main as start_ctrl
        start_ctrl()
    elif choice == "2":
        from scripts.start_agent import main as start_ag
        start_ag()
    elif choice == "3":
        url = input("  请输入主控端地址 [默认: http://127.0.0.1:8000]: ").strip()
        if not url:
            url = "http://127.0.0.1:8000"
        print(f"  正在打开浏览器访问: {url}")
        webbrowser.open(url)
    elif choice == "4":
        show_roles_guide()
        input("  按 Enter 键返回主菜单...")
        main()
    else:
        print("已退出。")


if __name__ == "__main__":
    main()
