"""
Launch script for ChemCompute Agent node daemon.
"""

import argparse
import sys
from pathlib import Path

# Ensure package root is in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chemcompute.agent.daemon import AgentDaemon


def main():
    parser = argparse.ArgumentParser(description="ChemCompute Agent Node Daemon")
    parser.add_argument("--controller", default="http://127.0.0.1:8000", help="Controller URL")
    parser.add_argument("--invite", default=None, help="Invite code for node enrollment")
    parser.add_argument("--name", default=None, help="Custom node display name")
    parser.add_argument("--interval", type=float, default=5.0, help="Heartbeat interval in seconds")
    parser.add_argument("--config", default=None, help="Path to node_config.json")
    parser.add_argument("--channel", default="stable", choices=["canary", "beta", "stable"], help="Rollout channel")
    parser.add_argument("--version", default="0.3.0", help="Agent version identifier")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else (ROOT / "node_config.json")
    workspace_dir = ROOT / "workspace"

    from chemcompute.common.models import ReleaseChannel
    daemon = AgentDaemon(
        controller_url=args.controller,
        config_path=config_path,
        workspace_dir=workspace_dir,
        node_name=args.name,
        heartbeat_interval=args.interval,
        agent_version=args.version,
        channel=ReleaseChannel(args.channel.lower()),
        agent_root_dir=ROOT
    )

    if not daemon.node_id:
        invite_code = args.invite
        if not invite_code:
            print("================================================================================")
            print("  💻 ChemCompute 普通计算节点 (Worker Node) 配置向导")
            print("================================================================================")
            print(f"  目标主控端地址: {args.controller}")
            invite_code = input("  请输入管理员密钥 / 邀请码 (如 CC-XXXXXX): ").strip()

        if not invite_code:
            print("❌ 未输入有效密钥，退出程序。")
            sys.exit(1)

        ok = daemon.enroll(invite_code)
        if not ok:
            print("节点入网失败，请核对邀请码及网络连接后重试。")
            sys.exit(1)

    # Start main loop
    daemon.run_loop()


if __name__ == "__main__":
    main()
