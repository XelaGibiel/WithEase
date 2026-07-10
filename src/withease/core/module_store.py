"""Curated module store.

Fetches a curated index of official modules (store/modules.json in the repo),
compares it against what is installed under %APPDATA%/WithEase/modules/, and
installs / removes modules on request.  This is deliberately a *curated* store:
the index only ever points at WithEase's own, controlled downloads – it must
never load third-party code from arbitrary URLs.

Reuses the download/version helpers from :mod:`withease.core.updater` and the
install location from :mod:`withease.core.module_loader`.
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from withease import __version__
from withease.core import updater
from withease.core.module_loader import MODULES_DIR

log = logging.getLogger(__name__)

# Curated index, hosted in the repo.  Raw GitHub URL so it needs no release.
INDEX_URL = (
    f"https://raw.githubusercontent.com/{updater.GITHUB_REPO}"
    "/main/store/modules.json")

_TIMEOUT = 10   # seconds for the index fetch


@dataclass
class StoreModule:
    id: str
    name: str
    version: str
    author: str
    description: str
    download_url: str
    subdir: str            # folder within the archive, "" = archive root
    min_app_version: str

    # Filled in against the local install state:
    installed_version: str | None = None

    @property
    def installed(self) -> bool:
        return self.installed_version is not None

    @property
    def update_available(self) -> bool:
        return (self.installed
                and updater.is_newer(self.version, self.installed_version))

    @property
    def compatible(self) -> bool:
        # min_app_version must not be newer than the running app.
        return not updater.is_newer(self.min_app_version, __version__)


# ---------------------------------------------------------------------------
# Index + local state
# ---------------------------------------------------------------------------

def _installed_version(module_id: str) -> str | None:
    """Version from an installed module's manifest, or None if not installed."""
    manifest = MODULES_DIR / module_id / "manifest.json"
    if not manifest.exists():
        return None
    try:
        with open(manifest, encoding="utf-8") as f:
            return str(json.load(f).get("version", "0"))
    except Exception:
        return "0"   # installed but unreadable manifest – treat as present


def _parse_index(data: dict) -> list[StoreModule]:
    modules: list[StoreModule] = []
    for entry in data.get("modules", []):
        try:
            mod = StoreModule(
                id=str(entry["id"]),
                name=str(entry.get("name", entry["id"])),
                version=str(entry.get("version", "0")),
                author=str(entry.get("author", "")),
                description=str(entry.get("description", "")),
                download_url=str(entry["download_url"]),
                subdir=str(entry.get("subdir", "")),
                min_app_version=str(entry.get("min_app_version", "0")),
            )
        except (KeyError, TypeError):
            log.warning("skipping malformed store entry: %r", entry)
            continue
        mod.installed_version = _installed_version(mod.id)
        modules.append(mod)
    return modules


def _local_index() -> list[StoreModule] | None:
    """The curated index bundled in the repo, when running from a checkout.

    Lets the store work fully offline during development/preview: `python -m
    withease` from the repo shows and installs modules without a network or a
    published index.  Ignored in an installed (non-git) deployment.
    """
    root = updater._repo_root()
    if root is None:
        return None
    path = root / "store" / "modules.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_index(json.load(f))
    except Exception:
        log.info("local store index unreadable", exc_info=True)
        return None


def fetch_index() -> list[StoreModule] | None:
    """Fetch and parse the curated index (blocking).  None on any failure.

    Prefers the local repo index when running from a checkout, so the store is
    immediately usable before the index is published; otherwise fetches the
    published index over the network.
    """
    local = _local_index()
    if local is not None:
        return local
    try:
        req = urllib.request.Request(
            INDEX_URL, headers={"User-Agent": "WithEase"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.load(resp)
        return _parse_index(data)
    except Exception as exc:
        # Expected when offline or before the index is published (404) –
        # handled quietly, no traceback spam in the user's log.
        log.info("store index fetch skipped: %s", exc)
        return None


def fetch_index_async(callback: Callable[[list[StoreModule] | None], None]) -> None:
    """Fetch the index off the UI thread.  Callback runs on a worker thread –
    marshal to the Qt main thread before touching widgets."""
    threading.Thread(
        target=lambda: callback(fetch_index()),
        daemon=True, name="store-index").start()


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def install(module: StoreModule) -> None:
    """Download and install a module (blocking, raises on failure).

    The archive is downloaded, the module folder (``subdir`` within it, or the
    archive root) located, validated to contain a manifest.json, and copied to
    %APPDATA%/WithEase/modules/<id>/.  A restart picks it up.

    When running from a checkout, the module is copied straight from the local
    ``examples/`` folder instead – no download needed.
    """
    root = updater._repo_root()
    if root is not None and module.subdir:
        local_src = root / module.subdir
        if (local_src / "manifest.json").exists():
            _place(local_src, module.id)
            return

    req = urllib.request.Request(
        module.download_url, headers={"User-Agent": "WithEase"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            zf.extractall(tmp)

        src = _locate_module_folder(Path(tmp), module.subdir)
        if src is None or not (src / "manifest.json").exists():
            raise RuntimeError("Modul im Archiv nicht gefunden "
                               "(manifest.json fehlt)")
        _place(src, module.id)


def _place(src: Path, module_id: str) -> None:
    """Copy a validated module folder to the install location, replacing any
    previous install so updates are clean.  Ignores __pycache__."""
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODULES_DIR / module_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest,
                    ignore=shutil.ignore_patterns("__pycache__"))


def _locate_module_folder(root: Path, subdir: str) -> Path | None:
    """Find the module folder inside an extracted archive.

    Repo zipballs wrap everything in a single top-level folder
    (``WithEase-main/``), so we glob one level down.  ``subdir`` may be a
    path like ``examples/hydration``.  Without a subdir we take the archive's
    single top-level folder.
    """
    if subdir:
        matches = list(root.glob(f"*/{subdir}"))
        if matches:
            return matches[0]
        direct = root / subdir
        return direct if direct.exists() else None
    tops = [p for p in root.iterdir() if p.is_dir()]
    return tops[0] if len(tops) == 1 else root


def uninstall(module_id: str) -> bool:
    """Remove an installed module's folder.  True if something was removed."""
    dest = MODULES_DIR / module_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
        return True
    return False
