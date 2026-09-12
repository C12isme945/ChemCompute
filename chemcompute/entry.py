"""Installer onboarding and per-user background lifecycle."""
from __future__ import annotations

import configparser
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from chemcompute.common.config import (
    ControllerConfig,
    NodeConfig,
    load_yaml_config,
    save_yaml_config,
)
from chemcompute.common.security import ensure_secure_directory


def runtime_home() -> Path:
    base = os.environ.get("CHEMCOMPUTE_HOME")
    if base:
        return Path(base).resolve()
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChemComputeData"


def onboard(path: str) -> None:
    from chemcompute.common.security import generate_invite_code
    from chemcompute.controller.database import Database

    ini = configparser.ConfigParser(interpolation=None)
    raw = Path(path).read_bytes()
    ini.read_string(raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"))
    v = ini["setup"]
    role = v.get("role", "both")
    if role not in {"controller", "node", "both"}:
        raise ValueError("Invalid role")
    ensure_secure_directory("config")
    ensure_secure_directory("data")
    if role in {"controller", "both"}:
        from chemcompute.common.security import load_or_create_runtime_admin_secret
        load_or_create_runtime_admin_secret()
        if not Path("config/controller.yaml").exists():
            save_yaml_config(ControllerConfig(host=v.get("host", "127.0.0.1")), "config/controller.yaml")
    if role in {"node", "both"} and not Path("config/node.yaml").exists():
        invite = v.get("invite", "") or None
        url = v.get("url", "http://127.0.0.1:8000")
        if role == "both":
            cfg = load_yaml_config("config/controller.yaml", ControllerConfig)
            url = f"http://{cfg.host}:{cfg.port}"
            invite = generate_invite_code()
            db = Database(cfg.db_path)
            db.create_invite_code(invite, max_uses=10)
        save_yaml_config(NodeConfig(controller_url=url, invite_code=invite,
                                    node_name=v.get("name", "")), "config/node.yaml")
    Path("role.json").write_text(json.dumps({"role": role}), encoding="utf-8")


def desktop_run() -> int:
    import hashlib
    import socket

    port = 40000 + int(hashlib.sha256(str(Path.cwd()).encode()).hexdigest()[:4], 16) % 20000
    guard = socket.socket()
    try:
        guard.bind(("127.0.0.1", port))
    except OSError:
        return 0
    import atexit

    from chemcompute.desktop_backend import clear_own_record, record_process

    record_process()
    atexit.register(clear_own_record)
    ensure_secure_directory("logs")
    logging.basicConfig(filename="logs/desktop.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    role = json.loads(Path("role.json").read_text("utf-8"))["role"]
    if role in {"controller", "both"}:
        from chemcompute.common.security import load_or_create_runtime_admin_secret
        load_or_create_runtime_admin_secret()

    if role in {"node", "both"}:
        def node_loop():
            from chemcompute.agent.daemon import AgentDaemon
            while True:
                try:
                    cfg = load_yaml_config("config/node.yaml", NodeConfig)
                    daemon = AgentDaemon(
                        controller_url=cfg.controller_url,
                        config_path=Path("config/node_agent.json"),
                        workspace_dir=Path("data/workspace"),
                        node_name=cfg.node_name,
                        heartbeat_interval=2.0,
                        agent_version="0.3.0"
                    )
                    if cfg.invite_code and not daemon.node_id:
                        daemon.enroll(cfg.invite_code)
                    daemon.run_loop()
                except Exception:
                    logging.exception("Node stopped; retrying in 10 seconds")
                time.sleep(10)

        threading.Thread(target=node_loop, daemon=True).start()
    if role in {"controller", "both"}:
        import uvicorn
        from chemcompute.controller.app import app

        cfg = load_yaml_config("config/controller.yaml", ControllerConfig)
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None)
    else:
        while True:
            time.sleep(60)
    guard.close()
    return 0


def main() -> int:
    if getattr(sys, "frozen", False) or os.environ.get("CHEMCOMPUTE_HOME"):
        directory = ensure_secure_directory(runtime_home())
        os.chdir(directory)
    if len(sys.argv) > 1 and sys.argv[1] == "onboard":
        onboard(sys.argv[2])
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "desktop-run":
        return desktop_run()
    if len(sys.argv) > 1 and sys.argv[1] == "console":
        from chemcompute.desktop import run_console
        return run_console(smoke_test='--smoke-test' in sys.argv)
    from chemcompute.cli import main as cli_main
    return cli_main()
