"""JSON-based configuration manager.

Handles loading and saving of all settings and profiles.
Each profile is stored as a separate JSON file under the user's config directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AccessMate"
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
            "keyboard_click_left": "",
            "keyboard_click_right": "",
            "keyboard_click_double": "",
            "screen_zones_enabled": False,
            "screen_zones": [],
        },
        "keyboard": {
            "enabled": False,
            "delay_enabled": False,
            "delay_ms": 500,
            "delay_exceptions": [],
            "sticky_shift": False,
            "sticky_ctrl": False,
            "sticky_alt": False,
            "sticky_win": False,
            "sticky_auto_release": True,
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


def save_app_config(config: dict[str, Any]) -> None:
    ensure_dirs()
    with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_profile(name: str) -> dict[str, Any]:
    ensure_dirs()
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        profile = DEFAULT_PROFILE.copy()
        profile["name"] = name.capitalize()
        save_profile(name, profile)
        return profile
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data


def save_profile(name: str, profile: dict[str, Any]) -> None:
    ensure_dirs()
    path = PROFILES_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def list_profiles() -> list[str]:
    ensure_dirs()
    return [p.stem for p in PROFILES_DIR.glob("*.json")]


def delete_profile(name: str) -> None:
    path = PROFILES_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
