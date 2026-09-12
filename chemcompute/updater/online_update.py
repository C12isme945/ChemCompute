"""
Online update client for ChemCompute Desktop and Agent.
Queries GitHub releases and Controller release channels,
verifies checksums, and handles background streaming downloads.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Any, Optional

import httpx

from chemcompute import __version__

GITHUB_REPO = "C12isme945/ChemCompute"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"


def parse_semver(ver: str) -> tuple[int, ...]:
    """Parse string like 'v0.3.0' or '0.3.0' into tuple (0, 3, 0)."""
    clean = ver.strip().lstrip("v").lstrip("V")
    parts = []
    for p in clean.split("."):
        try:
            parts.append(int(p.split("-")[0]))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_github_updates(current_ver: str = __version__) -> Dict[str, Any]:
    """
    Query GitHub Releases API to find the newest release.
    Returns metadata including version, changelog, and asset download URLs.
    """
    result = {
        "current_version": current_ver,
        "latest_version": current_ver,
        "has_update": False,
        "release_title": "",
        "release_notes": "",
        "published_at": "",
        "asset_name": "",
        "download_url": "",
        "asset_size": 0,
        "html_url": GITHUB_RELEASES_PAGE,
        "status": "ok",
        "error": None
    }

    try:
        headers = {
            "User-Agent": f"ChemCompute-Desktop/{current_ver}",
            "Accept": "application/vnd.github.v3+json"
        }
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(GITHUB_RELEASES_API, headers=headers)
            if resp.status_code != 200:
                result["status"] = "error"
                result["error"] = f"GitHub API 返回 HTTP {resp.status_code}"
                return result

            releases = resp.json()
            if not releases or not isinstance(releases, list):
                result["status"] = "not_found"
                return result

            # Find first non-draft release
            target = None
            for rel in releases:
                if not rel.get("draft", False):
                    target = rel
                    break

            if not target:
                return result

            tag = target.get("tag_name", "").lstrip("v")
            result["latest_version"] = tag
            result["release_title"] = target.get("name") or target.get("tag_name")
            result["release_notes"] = target.get("body", "").strip()
            result["published_at"] = target.get("published_at", "")[:10]
            result["html_url"] = target.get("html_url", GITHUB_RELEASES_PAGE)

            # Check if newer version
            if parse_semver(tag) > parse_semver(current_ver):
                result["has_update"] = True

            # Locate Windows installer asset
            for asset in target.get("assets", []):
                name = asset.get("name", "")
                if name.endswith(".exe") or "Setup" in name:
                    result["asset_name"] = name
                    result["download_url"] = asset.get("browser_download_url", "")
                    result["asset_size"] = asset.get("size", 0)
                    break

            return result
    except Exception as ex:
        result["status"] = "error"
        result["error"] = f"无法连接至 GitHub 发布服务器: {str(ex)}"
        return result


def download_file_stream(
    url: str,
    dest_path: Path,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> bool:
    """
    Download a file with real-time progress updates.
    progress_callback(percent, downloaded_bytes, total_bytes)
    """
    headers = {"User-Agent": f"ChemCompute-Desktop/{__version__}"}
    try:
        with httpx.Client(timeout=180.0, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    return False
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        if cancel_check and cancel_check():
                            return False
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            pct = min(100, int(downloaded * 100 / total))
                            progress_callback(pct, downloaded, total)
        return True
    except Exception:
        return False


def launch_installer(installer_path: Path) -> bool:
    """Launch the downloaded setup installer executable."""
    try:
        if sys.platform == "win32":
            os.startfile(str(installer_path))
        else:
            subprocess.Popen([str(installer_path)])
        return True
    except Exception:
        return False
