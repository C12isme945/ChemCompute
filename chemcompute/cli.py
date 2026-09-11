"""ChemCompute 命令行入口 (CLI Entrypoint)

支持统一命令调度:
- chemcompute controller [run | init-config | invite]
- chemcompute node [run | init-config | start-bg | stop-bg | status-bg | install-task | uninstall-task]
- chemcompute setup / init-config (多角色向导与安装器静默配置生成)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from chemcompute import __version__
from chemcompute.common.config import (
    ControllerConfig,
    NodeConfig,
    load_yaml_config,
    save_yaml_config,
)
from chemcompute.common.network import resolve_listen_host
from chemcompute.common.security import (
    ensure_secure_directory,
    generate_invite_code,
    hash_secret,
)

logger = logging.getLogger("chemcompute.cli")


def setup_logging(verbose: bool = False) -> None:
    """配置统一日志输出格式"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ------------------- 控制端命令处理 -------------------


def handle_controller_run(args: argparse.Namespace) -> int:
    """启动控制端 Web & API 服务"""
    import uvicorn

    from chemcompute.controller.app import create_app

    config_path = Path(args.config) if args.config else Path("config/controller.yaml")
    config: ControllerConfig = load_yaml_config(config_path, ControllerConfig)

    # CLI 显式参数覆盖配置文件
    if args.host:
        config.host = args.host
    elif args.private_interface:
        config.host = resolve_listen_host(None, use_private_interface=True)
    if args.port:
        config.port = args.port
    if args.secret:
        config.admin_secret = args.secret

    app = create_app(config)
    print(f"[*] 启动 ChemCompute 控制中心 (http://{config.host}:{config.port}) ...")
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    return 0


def handle_controller_init_config(args: argparse.Namespace) -> int:
    """初始化控制端配置文件"""
    out_path = Path(args.output or "config/controller.yaml")
    host = resolve_listen_host(args.host, use_private_interface=args.private_interface)
    config = ControllerConfig(
        host=host,
        port=args.port or 8000,
        db_path="data/chemcompute.db",
        secret_file="data/chemcompute-admin.secret",
    )
    save_yaml_config(config, out_path)
    print(f"[+] 控制端配置已成功写入: {out_path} (Host: {config.host}, Port: {config.port})")
    return 0


def handle_controller_invite_create(args: argparse.Namespace) -> int:
    """控制端通过 CLI 直接生成单次注册邀请码"""
    from datetime import datetime, timedelta, timezone

    from chemcompute.controller.db import Database

    config_path = Path(args.config or "config/controller.yaml")
    config: ControllerConfig = load_yaml_config(config_path, ControllerConfig)
    db = Database(config.db_path)

    raw_code = generate_invite_code()
    code_hash = hash_secret(raw_code)
    code_prefix = raw_code[:12] + "..."
    expires_dt = datetime.now(timezone.utc) + timedelta(hours=args.expires or 24)
    expires_at = expires_dt.isoformat()

    db.create_invite(
        code_hash=code_hash,
        code_prefix=code_prefix,
        expires_at=expires_at,
        note=args.note or "CLI generated",
    )
    print("=" * 60)
    print("[+] 邀请码生成成功 (仅展示一次，请妥善转交给节点配置):")
    print(f"    邀请码: {raw_code}")
    print(f"    有效期至: {expires_at}")
    print(f"    备注: {args.note or 'CLI generated'}")
    print("=" * 60)
    return 0


def handle_controller_invite_list(args: argparse.Namespace) -> int:
    """列出邀请码"""
    from chemcompute.controller.db import Database

    config_path = Path(args.config or "config/controller.yaml")
    config: ControllerConfig = load_yaml_config(config_path, ControllerConfig)
    db = Database(config.db_path)
    invites = db.list_invites()
    print(f"[*] 共有 {len(invites)} 条邀请码记录:")
    for inv in invites:
        status_str = "已使用" if inv["is_used"] else ("已过期" if inv["is_expired"] else "有效")
        print(f" - [{status_str}] ID: {inv['id']} | 前缀: {inv['code_prefix']} | 过期时间: {inv['expires_at']} | 备注: {inv.get('note')}")
    return 0


# ------------------- 计算节点命令处理 -------------------


def handle_node_run(args: argparse.Namespace) -> int:
    """前台运行计算节点代理"""
    from chemcompute.node.agent import NodeAgent

    config_path = Path(args.config or "config/node.yaml")
    config: NodeConfig = load_yaml_config(config_path, NodeConfig)

    if args.controller_url:
        config.controller_url = args.controller_url
    if args.invite_code:
        config.invite_code = args.invite_code
    if args.name:
        config.node_name = args.name
    if args.gromacs_path:
        config.gromacs_custom_path = args.gromacs_path

    agent = NodeAgent(config=config, config_path=config_path)
    try:
        agent.run()
        return 0
    except KeyboardInterrupt:
        print("\n[*] 接收到中断信号，正在退出节点代理...")
        agent.stop()
        return 0


def handle_node_init_config(args: argparse.Namespace) -> int:
    """初始化节点配置文件"""
    out_path = Path(args.output or "config/node.yaml")
    config = NodeConfig(
        controller_url=args.controller_url or "http://127.0.0.1:8000",
        node_name=args.name or "",
        invite_code=args.invite_code or None,
        gromacs_custom_path=args.gromacs_path or None,
        workspace_dir="data/workspace",
    )
    save_yaml_config(config, out_path)
    print(f"[+] 节点配置已成功写入: {out_path}")
    return 0


def handle_node_bg_start(args: argparse.Namespace) -> int:
    """后台脱离进程模式启动节点"""
    from chemcompute.node.service import start_background

    config_path = args.config or "config/node.yaml"
    pid = start_background(config_path)
    print(f"[+] 节点代理已在后台启动 (PID: {pid})，日志记录于 logs/node-daemon.log")
    return 0


def handle_node_bg_stop(args: argparse.Namespace) -> int:
    """停止后台计算节点"""
    from chemcompute.node.service import stop_background

    stop_background()
    print("[+] 后台节点代理已停止")
    return 0


def handle_node_bg_status(args: argparse.Namespace) -> int:
    """查看后台节点运行状态"""
    from chemcompute.node.service import status_background

    st = status_background()
    print(f"[*] 节点后台状态: {st.get('message')} (Running: {st.get('running')}, PID: {st.get('pid')})")
    if st.get("cpu_percent") is not None:
        print(f"    CPU 占用: {st.get('cpu_percent')}% | 内存占用: {st.get('memory_mb')} MB")
    return 0


def handle_node_task_install(args: argparse.Namespace) -> int:
    """注册 Windows 登录自动运行计划任务"""
    from chemcompute.node.service import install_startup_task

    task_name = args.task_name or "ChemComputeNode"
    cfg = args.config or "config/node.yaml"
    ok = install_startup_task(task_name=task_name, config_path=cfg)
    if ok:
        print(f"[+] 成功注册 Windows 登录启动计划任务: {task_name}")
        return 0
    print("[-] 注册计划任务失败，请以管理员身份重试或检查系统设置")
    return 1


def handle_node_task_uninstall(args: argparse.Namespace) -> int:
    """卸载 Windows 登录启动计划任务"""
    from chemcompute.node.service import uninstall_startup_task

    task_name = args.task_name or "ChemComputeNode"
    ok = uninstall_startup_task(task_name=task_name)
    if ok:
        print(f"[+] 成功卸载 Windows 计划任务: {task_name}")
        return 0
    print("[-] 卸载计划任务失败")
    return 1


# ------------------- 统一引导 / 安装器配置入口 -------------------


def handle_setup(args: argparse.Namespace) -> int:
    """
    向导式或无人值守初始化 ChemCompute 全套配置 (支持 Controller, Node, Both)。
    专为 Inno Setup 安装向导及初次部署设计。无硬编码秘密。
    """
    role = (args.role or "both").lower()
    config_dir = Path(args.config_dir or "config")
    ensure_secure_directory(config_dir)
    ensure_secure_directory("data")
    ensure_secure_directory("logs")

    print("=" * 60)
    print(f" ChemCompute v{__version__} 安装与环境初始化向导")
    print(f" 部署角色: {role.upper()}")
    print("=" * 60)

    # 1. 控制端配置
    if role in ("controller", "both"):
        host = resolve_listen_host(args.host, use_private_interface=args.private_interface)
        ctrl_cfg = ControllerConfig(
            host=host,
            port=args.port or 8000,
            db_path="data/chemcompute.db",
            secret_file="data/chemcompute-admin.secret",
        )
        ctrl_path = config_dir / "controller.yaml"
        save_yaml_config(ctrl_cfg, ctrl_path)
        print(f"[+] 控制端配置已生成: {ctrl_path} (监听: http://{ctrl_cfg.host}:{ctrl_cfg.port})")

    # 2. 节点配置
    if role in ("node", "both"):
        ctrl_url = args.controller_url or (
            f"http://127.0.0.1:{args.port or 8000}" if role == "both" else "http://127.0.0.1:8000"
        )
        node_cfg = NodeConfig(
            controller_url=ctrl_url,
            node_name=args.node_name or "",
            invite_code=args.invite_code or None,
            workspace_dir="data/workspace",
        )
        node_path = config_dir / "node.yaml"
        save_yaml_config(node_cfg, node_path)
        print(f"[+] 节点配置已生成: {node_path} (关联控制端: {node_cfg.controller_url})")

    print("[+] 初始化完成! 敏感目录权限及安全 ACL 已锁定。")
    return 0


# ------------------- 主解析器装配 -------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chemcompute",
        description=f"ChemCompute v{__version__} - 现代化化学计算与GROMACS分布式节点调度平台",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="启用详细调试日志")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="subcommand", help="子命令模块")

    # 1. controller
    ctrl_parser = subparsers.add_parser("controller", help="控制中心管理与运行")
    ctrl_subs = ctrl_parser.add_subparsers(dest="action")

    c_run = ctrl_subs.add_parser("run", help="启动控制中心 Web & API 服务")
    c_run.add_argument("--config", "-c", help="配置文件路径 (默认: config/controller.yaml)")
    c_run.add_argument("--host", help="监听地址 (默认 127.0.0.1 本地环回)")
    c_run.add_argument("--port", "-p", type=int, help="监听端口 (默认 8000)")
    c_run.add_argument("--secret", help="显式指定管理员运行时口令")
    c_run.add_argument("--private-interface", action="store_true", help="自动绑定第一个私有局域网接口")

    c_init = ctrl_subs.add_parser("init-config", help="生成控制中心默认配置文件")
    c_init.add_argument("--output", "-o", help="输出配置文件路径")
    c_init.add_argument("--host", help="监听地址")
    c_init.add_argument("--port", "-p", type=int, help="监听端口")
    c_init.add_argument("--private-interface", action="store_true", help="自动侦测局域网私有IP")

    c_inv = ctrl_subs.add_parser("invite", help="邀请码管理")
    c_inv_subs = c_inv.add_subparsers(dest="invite_action")
    c_inv_create = c_inv_subs.add_parser("create", help="生成单次使用入网邀请码")
    c_inv_create.add_argument("--config", "-c", help="控制端配置文件路径")
    c_inv_create.add_argument("--expires", "-e", type=int, default=24, help="有效时长(小时)")
    c_inv_create.add_argument("--note", "-n", help="邀请备注信息")
    c_inv_list = c_inv_subs.add_parser("list", help="查看所有邀请码记录")
    c_inv_list.add_argument("--config", "-c", help="控制端配置文件路径")

    # 2. node
    node_parser = subparsers.add_parser("node", help="计算节点工作代理")
    node_subs = node_parser.add_subparsers(dest="action")

    n_run = node_subs.add_parser("run", help="前台运行节点代理")
    n_run.add_argument("--config", "-c", help="节点配置文件路径 (默认: config/node.yaml)")
    n_run.add_argument("--controller-url", help="控制中心 URL 地址")
    n_run.add_argument("--invite-code", help="首次入网注册邀请码")
    n_run.add_argument("--name", help="节点自定义名称")
    n_run.add_argument("--gromacs-path", help="GROMACS 二进制可执行文件自定义路径")

    n_init = node_subs.add_parser("init-config", help="生成节点默认配置文件")
    n_init.add_argument("--output", "-o", help="输出配置文件路径")
    n_init.add_argument("--controller-url", help="控制中心 URL")
    n_init.add_argument("--invite-code", help="邀请码")
    n_init.add_argument("--name", help="节点名称")
    n_init.add_argument("--gromacs-path", help="GROMACS 路径")

    n_bg_start = node_subs.add_parser("start-bg", help="后台脱离进程模式启动节点")
    n_bg_start.add_argument("--config", "-c", help="配置文件路径")

    node_subs.add_parser("stop-bg", help="停止后台运行的节点")
    node_subs.add_parser("status-bg", help="检查后台节点状态")

    n_task_in = node_subs.add_parser("install-task", help="注册为 Windows 登录计划任务")
    n_task_in.add_argument("--config", "-c", help="配置文件路径")
    n_task_in.add_argument("--task-name", default="ChemComputeNode", help="任务名称")

    n_task_un = node_subs.add_parser("uninstall-task", help="注销 Windows 计划任务")
    n_task_un.add_argument("--task-name", default="ChemComputeNode", help="任务名称")

    # 3. setup / init-config
    setup_parser = subparsers.add_parser("setup", help="安装与初始化向导")
    setup_parser.add_argument("--role", choices=["controller", "node", "both"], default="both", help="部署角色")
    setup_parser.add_argument("--host", help="控制端监听地址")
    setup_parser.add_argument("--port", type=int, help="控制端端口")
    setup_parser.add_argument("--private-interface", action="store_true", help="使用私有网卡 IP")
    setup_parser.add_argument("--controller-url", help="节点关联的控制中心 URL")
    setup_parser.add_argument("--invite-code", help="节点注册邀请码")
    setup_parser.add_argument("--node-name", help="节点标识名称")
    setup_parser.add_argument("--config-dir", default="config", help="配置文件存储目录")
    setup_parser.add_argument("--non-interactive", action="store_true", help="无交互静默模式")

    # init-config 别名指向 setup
    subparsers.add_parser("init-config", parents=[setup_parser], add_help=False)

    return parser


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.subcommand == "controller":
        if args.action == "run":
            return handle_controller_run(args)
        elif args.action == "init-config":
            return handle_controller_init_config(args)
        elif args.action == "invite":
            if getattr(args, "invite_action", None) == "create":
                return handle_controller_invite_create(args)
            elif getattr(args, "invite_action", None) == "list":
                return handle_controller_invite_list(args)
            else:
                parser.parse_args(["controller", "invite", "--help"])
                return 0
        else:
            parser.parse_args(["controller", "--help"])
            return 0

    elif args.subcommand == "node":
        if args.action == "run":
            return handle_node_run(args)
        elif args.action == "init-config":
            return handle_node_init_config(args)
        elif args.action == "start-bg":
            return handle_node_bg_start(args)
        elif args.action == "stop-bg":
            return handle_node_bg_stop(args)
        elif args.action == "status-bg":
            return handle_node_bg_status(args)
        elif args.action == "install-task":
            return handle_node_task_install(args)
        elif args.action == "uninstall-task":
            return handle_node_task_uninstall(args)
        else:
            parser.parse_args(["node", "--help"])
            return 0

    elif args.subcommand in ("setup", "init-config"):
        return handle_setup(args)

    return 0


def main_controller() -> int:
    """控制端快捷入口 (chemcompute-controller)"""
    sys.argv.insert(1, "controller")
    if len(sys.argv) == 2:
        sys.argv.append("run")
    return main()


def main_node() -> int:
    """节点端快捷入口 (chemcompute-node)"""
    sys.argv.insert(1, "node")
    if len(sys.argv) == 2:
        sys.argv.append("run")
    return main()


if __name__ == "__main__":
    sys.exit(main())
