"""
CLI Entrypoint for ChemCompute Independent Updater.
Run as a separate process to perform zero-downtime / safe atomic updates.
"""

import argparse
import sys
from pathlib import Path

# Ensure root in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chemcompute.updater.updater import ChemComputeUpdater


def main():
    parser = argparse.ArgumentParser(description="ChemCompute Standalone Updater")
    parser.add_argument("--agent-dir", required=True, help="Path to agent root directory containing current/previous")
    parser.add_argument("--package", required=True, help="Download URL or path to new package zip")
    parser.add_argument("--sha256", default="", help="Expected SHA256 checksum")
    parser.add_argument("--version", required=True, help="Target update version")
    parser.add_argument("--agent-pid", type=int, default=None, help="PID of old agent process to wait for")
    parser.add_argument("--health-url", default=None, help="Health check endpoint URL")
    parser.add_argument("--launch-cmd", nargs="*", default=None, help="Command arguments to start new agent")
    parser.add_argument("--timeout", type=float, default=30.0, help="Health check timeout in seconds")
    args = parser.parse_args()

    updater = ChemComputeUpdater(agent_dir=Path(args.agent_dir))
    success = updater.perform_update(
        package_source=args.package,
        expected_sha256=args.sha256,
        target_version=args.version,
        agent_pid=args.agent_pid,
        launch_cmd=args.launch_cmd,
        health_url=args.health_url,
        timeout=args.timeout
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
