"""
Launch script for ChemCompute Controller.
"""

import sys
from pathlib import Path
import uvicorn

# Ensure package root is in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    import argparse
    from chemcompute.controller.app import db
    parser = argparse.ArgumentParser(description="ChemCompute Controller Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    args = parser.parse_args()

    lan_ip = get_lan_ip()
    admin_key = db.get_or_create_default_admin_code()

    print(f"================================================================================")
    print(f"  👑 ChemCompute Controller (管理员主控中心) 正在启动")
    print(f"================================================================================")
    print(f"  🌐 本机 Web 控制台:    http://127.0.0.1:{args.port}")
    print(f"  🌐 局域网访问地址:    http://{lan_ip}:{args.port}")
    print(f"  🔑 管理员密钥 / 邀请码: {admin_key}")
    print(f"--------------------------------------------------------------------------------")
    print(f"  💻 组员 / Worker 节点接入命令 (复制到其他电脑运行):")
    print(f"     python scripts/start_agent.py --controller http://{lan_ip}:{args.port} --invite {admin_key}")
    print(f"================================================================================\n")

    uvicorn.run(
        "chemcompute.controller.app:app",
        host=args.host,
        port=args.port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
