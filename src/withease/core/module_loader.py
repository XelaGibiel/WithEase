"""Loader for external (third-party) modules.

External modules live in %APPDATA%/WithEase/modules/, one folder each:

    modules/
      my_module/
        manifest.json     – metadata (see below)
        module.py         – entry file containing the module class

manifest.json:
    {
      "name":        "Mein Modul",
      "version":     "1.0.0",
      "author":      "Jane Doe",
      "description": "Was das Modul tut",
      "entry":       "module.py",
      "class":       "MyModule"
    }

The class must inherit withease.modules.base.BaseModule.  A module that
fails to load is skipped and logged – it can never take the app down.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys

from withease.core import config
from withease.modules.base import BaseModule

_log = logging.getLogger(__name__)

MODULES_DIR = config.CONFIG_DIR / "modules"


def discover_external_modules() -> list[BaseModule]:
    """Load every valid module from the external modules directory."""
    modules: list[BaseModule] = []
    try:
        MODULES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return modules

    for folder in sorted(MODULES_DIR.iterdir()):
        manifest_path = folder / "manifest.json"
        if not folder.is_dir() or not manifest_path.exists():
            continue
        try:
            modules.append(_load_module(folder, manifest_path))
            _log.info("external module loaded: %s", folder.name)
        except Exception:
            _log.exception("failed to load external module %r – skipped",
                           folder.name)
    return modules


def _load_module(folder, manifest_path) -> BaseModule:
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    class_name = manifest["class"]
    entry = folder / manifest.get("entry", "module.py")
    if not entry.exists():
        raise FileNotFoundError(f"entry file missing: {entry}")

    # Import the entry file under a unique, collision-free module name.
    spec = importlib.util.spec_from_file_location(
        f"withease_external.{folder.name}", entry)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import spec for {entry}")
    py_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = py_module
    spec.loader.exec_module(py_module)

    cls = getattr(py_module, class_name)
    instance = cls()
    if not isinstance(instance, BaseModule):
        raise TypeError(f"{class_name} does not inherit BaseModule")
    if not instance.MODULE_ID:
        raise ValueError(f"{class_name} has no MODULE_ID")

    # Attach manifest metadata for display purposes (About/Modules page).
    instance.MANIFEST = manifest  # type: ignore[attr-defined]
    return instance
