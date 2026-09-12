"""Start an already-provisioned local Cloudflare tunnel; no account credentials in builds."""
import os
import shutil
import subprocess
from pathlib import Path

import psutil


def start_configured_tunnel():
    config = Path('cloudflare/config.yml').resolve()
    if not config.is_file():
        return False
    executable = shutil.which('cloudflared.exe')
    if not executable:
        candidate = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'cloudflared/cloudflared.exe'
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise RuntimeError('Configured tunnel requires cloudflared on this controller')
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            if (process.info['name'] or '').lower() == 'cloudflared.exe' and str(config) in (process.info['cmdline'] or []):
                return True
        except psutil.Error:
            pass
    subprocess.Popen([executable, 'tunnel', '--config', str(config), 'run'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return True
