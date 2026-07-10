"""JSON-based configuration manager.

Handles loading and saving of all settings and profiles.
Each profile is stored as a separate JSON file under the user's config directory.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    """Where profiles and settings are stored.

    Normally %APPDATA%/WithEase.  Set the WITHEASE_CONFIG_DIR environment
    variable to use a different folder – e.g. to try WithEase as a brand-new
    user (point it at an empty folder) without touching your real settings.
    """
    override = os.environ.get("WITHEASE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("APPDATA", Path.home())) / "WithEase"


CONFIG_DIR = _config_dir()
PROFILES_DIR = CONFIG_DIR / "profiles"
APP_CONFIG_FILE = CONFIG_DIR / "app.json"

DEFAULT_APP_CONFIG: dict[str, Any] = {
    "active_profile": "default",
    "first_run": True,
    "language": "de",
    "theme": "system",
    "autostart": False,
    "tray_icon": True,
}

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "Default",
    "modules": {
        "mouse": {
            "enabled": False,
            "centering_enabled": False,
            "centering_delay": 5.0,
            "centering_countdown": 3,
            "centering_tolerance": 50,
            "precision_mode_enabled": False,
            "precision_speed": 3,
            "click_lock_enabled": False,
            "keyboard_clicks_enabled": False,
            "keyboard_click_left": "",
            "keyboard_click_right": "",
            "keyboard_click_double": "",
            "screen_zones_enabled": False,
            "screen_zone_1_hotkey": "", "screen_zone_2_hotkey": "",
            "screen_zone_3_hotkey": "", "screen_zone_4_hotkey": "",
            "screen_zone_5_hotkey": "", "screen_zone_6_hotkey": "",
            "screen_zone_7_hotkey": "", "screen_zone_8_hotkey": "",
            "screen_zone_9_hotkey": "",
        },
        "keyboard": {
            "enabled": False,
            "delay_enabled": False,
            "delay_ms": 500,
            "delay_exceptions": [],
            "sticky_enabled": False,
            "sticky_shift": False,
            "sticky_ctrl": False,
            "sticky_alt": False,
            "sticky_altgr": False,
            "sticky_win": False,
            "sticky_auto_release": True,
            "sticky_indicator_position": "bottom-right",
            "sticky_chip_size": 24,
            "show_modifier_status": True,
        },
        "macros": {
            "enabled": False,
            "trigger_key": "",
            "macros": [],
        },
    },
    "actions": {},
    "emergency_key": "F12",
}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def load_app_config() -> dict[str, Any]:
    ensure_dirs()
    if not APP_CONFIG_FILE.exists():
        save_app_config(DEFAULT_APP_CONFIG.copy())
        return DEFAULT_APP_CONFIG.copy()
    with open(APP_CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {**DEFAULT_APP_CONFIG, **data}


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically: temp file → rename, so a kill mid-write is safe."""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_app_config(config: dict[str, Any]) -> None:
    ensure_dirs()
    _atomic_write(APP_CONFIG_FILE, config)


def load_profile(name: str) -> dict[str, Any]:
    ensure_dirs()
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        profile = DEFAULT_PROFILE.copy()
        profile["name"] = name.capitalize()
        save_profile(name, profile)
        return profile
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupted file (e.g. killed mid-write) – restore from default
        profile = DEFAULT_PROFILE.copy()
        profile["name"] = name.capitalize()
        save_profile(name, profile)
        return profile


def save_profile(name: str, profile: dict[str, Any]) -> None:
    ensure_dirs()
    path = PROFILES_DIR / f"{name}.json"
    _atomic_write(path, profile)
    # Verify the write actually landed (security software may silently roll
    # back writes from processes it deems suspicious, e.g. keyboard hooks).
    import logging
    try:
        with open(path, encoding="utf-8") as f:
            on_disk = json.load(f)
        if on_disk != profile:
            logging.getLogger(__name__).error(
                "profile write to %s did NOT persist (content mismatch) – "
                "likely blocked/rolled back by security software", path)
        else:
            logging.getLogger(__name__).info("profile write verified: %s", path)
    except Exception:
        logging.getLogger(__name__).exception(
            "verifying profile write to %s failed", path)


def list_profiles() -> list[str]:
    ensure_dirs()
    return [p.stem for p in PROFILES_DIR.glob("*.json")]


def delete_profile(name: str) -> None:
    path = PROFILES_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
