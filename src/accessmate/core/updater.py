"""Update check and self-update via GitHub releases.

The check runs in a background thread and stays silent on any network
problem – the app must never depend on connectivity.  The actual update
prefers ``git pull`` when running from a git checkout; otherwise the
release zipball is downloaded and the package files are replaced in
place (running .py files are not locked on Windows).  Afterwards the
caller restarts the app.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from accessmate import __version__

log = logging.getLogger(__name__)

GITHUB_REPO = "XelaGibiel/AccessMate"
_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

_TIMEOUT = 10  # seconds


@dataclass
class ReleaseInfo:
    version: str          # e.g. "0.2.0"
    notes: str            # release body (markdown/plain text)
    html_url: str         # release page for manual download
    zipball_url: str      # source archive for the in-place update


def _parse_version(text: str) -> tuple[int, ...]:
    text = text.strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return _parse_version(candidate) > _parse_version(current)


def fetch_latest() -> ReleaseInfo | None:
    """Fetch the latest release (blocking).  None on any failure."""
    try:
        req = urllib.request.Request(
            _API_LATEST, headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": "AccessMate"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.load(resp)
        return ReleaseInfo(
            version=str(data.get("tag_name", "")).lstrip("vV"),
            notes=str(data.get("body", "") or ""),
            html_url=str(data.get("html_url", RELEASES_URL)),
            zipball_url=str(data.get("zipball_url", "")),
        )
    except Exception as exc:
        # Expected when offline or before the first GitHub Release exists
        # (404).  Handled silently – no traceback spam in the user's log.
        log.info("update check skipped: %s", exc)
        return None


def check_async(callback: Callable[[ReleaseInfo | None], None]) -> None:
    """Check for a newer release without blocking the UI.

    ``callback`` receives the ReleaseInfo when a NEWER version exists,
    otherwise None.  It is invoked from a worker thread – marshal to the
    Qt main thread before touching widgets.
    """
    def run() -> None:
        info = fetch_latest()
        if info and info.version and is_newer(info.version):
            callback(info)
        else:
            callback(None)

    threading.Thread(target=run, daemon=True, name="update-check").start()


# ---------------------------------------------------------------------------
# Performing the update
# ---------------------------------------------------------------------------

def _repo_root() -> Path | None:
    """Project root when running from a git checkout, else None."""
    pkg = Path(__file__).resolve().parents[2]   # …/src
    root = pkg.parent
    return root if (root / ".git").exists() else None


def _update_via_git(root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def _update_via_zipball(info: ReleaseInfo) -> None:
    """Download the release source and replace the installed package."""
    import io
    import shutil
    import tempfile
    import zipfile

    if not info.zipball_url:
        raise RuntimeError("no download URL in release")
    req = urllib.request.Request(
        info.zipball_url, headers={"User-Agent": "AccessMate"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()

    pkg_dir = Path(__file__).resolve().parents[1]   # …/accessmate
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            zf.extractall(tmp)
        # The zipball contains one top-level folder with the repo contents.
        candidates = list(Path(tmp).glob("*/src/accessmate"))
        if not candidates:
            raise RuntimeError("unexpected archive layout")
        shutil.copytree(candidates[0], pkg_dir, dirs_exist_ok=True)


def perform_update(info: ReleaseInfo) -> None:
    """Apply the update (blocking, raises on failure)."""
    root = _repo_root()
    if root is not None:
        _update_via_git(root)
    else:
        _update_via_zipball(info)


def restart_app() -> None:
    """Relaunch AccessMate and quit the running instance.

    The relaunch is delayed by a helper process so the single-instance
    mutex of THIS process is released before the new one checks it.
    """
    subprocess.Popen(
        [sys.executable, "-c",
         "import time, subprocess, sys; time.sleep(2); "
         "subprocess.Popen([sys.executable, '-m', 'accessmate'])"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.quit()
