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
    from datetime import datetime, timedelta, timezone

    from chemcompute.common.security import generate_invite_code, hash_secret
    from chemcompute.controller.db import Database

    ini = configparser.ConfigParser(interpolation=None)
    raw = Path(path).read_bytes()
    ini.read_string(raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"))
    v = ini["setup"]
    role = v.get("role", "both")
    if role not in {"controller", "node", "both"}:
        raise ValueError("Invalid role")
    ensure_secure_directory("config")
    ensure_secure_directory("data")
    if role in {"controller", "both"} and not Path("config/controller.yaml").exists():
        save_yaml_config(ControllerConfig(host=v.get("host", "127.0.0.1")), "config/controller.yaml")
    if role in {"node", "both"} and not Path("config/node.yaml").exists():
        invite = v.get("invite", "") or None
        url = v.get("url", "http://127.0.0.1:8000")
        if role == "both":
            cfg = load_yaml_config("config/controller.yaml", ControllerConfig)
            url = f"http://{cfg.host}:{cfg.port}"
            invite = generate_invite_code()
            db = Database(cfg.db_path)
            db.create_invite(hash_secret(invite), invite[:12] + "...",
                             (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "Local onboarding")
        save_yaml_config(NodeConfig(controller_url=url, invite_code=invite,
                                    node_name=v.get("name", "")), "config/node.yaml")
    Path("role.json").write_text(json.dumps({"role": role}), encoding="utf-8")


def desktop_run() -> int:
    import atexit

    from chemcompute.runtime_lock import acquire_runtime_lock
    release_lock = acquire_runtime_lock()
    if release_lock is None:
        return 0
    atexit.register(release_lock)

    from chemcompute.desktop_backend import clear_own_record, record_process

    record_process()
    atexit.register(clear_own_record)
    ensure_secure_directory("logs")
    logging.basicConfig(filename="logs/desktop.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    role = json.loads(Path("role.json").read_text("utf-8"))["role"]
    if role in {"node", "both"}:
        from chemcompute.node.agent import NodeAgent

        def node_loop():
            while True:
                try:
                    cfg = load_yaml_config("config/node.yaml", NodeConfig)
                    NodeAgent(cfg, "config/node.yaml").run()
                except Exception:
                    logging.exception("Node stopped; retrying in 15 seconds")
                time.sleep(15)

        threading.Thread(target=node_loop, daemon=True).start()
    if role in {"controller", "both"}:
        from chemcompute.tunnel import start_configured_tunnel
        try:
            start_configured_tunnel()
        except Exception:
            logging.exception("Configured public tunnel unavailable")
        import uvicorn

        from chemcompute.controller.app import create_app

        cfg = load_yaml_config("config/controller.yaml", ControllerConfig)
        uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_config=None)
    else:
        while True:
            time.sleep(60)
    return 0


def main() -> int:
    if getattr(sys, "frozen", False) or os.environ.get("CHEMCOMPUTE_HOME"):
        directory = ensure_secure_directory(runtime_home())
        os.chdir(directory)
    if len(sys.argv) > 1 and sys.argv[1] == "onboard":
        onboard(sys.argv[2])
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "update-finish":
        from chemcompute.online_update import complete_update
        complete_update(sys.argv[2])
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "desktop-run":
        return desktop_run()
    if len(sys.argv) > 1 and sys.argv[1] == "console":
        from chemcompute.desktop import run_console
        return run_console(smoke_test='--smoke-test' in sys.argv)
    from chemcompute.cli import main as cli_main
    return cli_main()
