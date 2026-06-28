"""Main application controller.

Owns the module registry, profile management, and the emergency stop.
The GUI and tray are created here and live for the entire app lifetime.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QApplication

from accessmate.core import config
from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.core import i18n
from accessmate.core.i18n import tr
from accessmate.modules.base import BaseModule
from accessmate.modules.keyboard import KeyboardModule
from accessmate.modules.macros import MacrosModule
from accessmate.modules.mouse import MouseModule
from accessmate.tray import TrayIcon


class AccessMateApp:
    def __init__(self, qt_app: QApplication) -> None:
        self._qt_app = qt_app
        self._app_config = config.load_app_config()
        i18n.load(self._app_config.get("language", "de"))
        self._active_profile: str = self._app_config.get("active_profile", "default")
        self._profile_data: dict[str, Any] = {}
        self._paused = False
        self._settings_window = None

        self._modules: list[BaseModule] = [
            MouseModule(),
            KeyboardModule(),
            MacrosModule(),
        ]

        action_manager.register(Action(
            id="app.emergency_stop",
            label=tr("app.emergency_stop"),
            callback=self.emergency_stop,
        ))

        self._tray = TrayIcon(self)
        self._tray.show()

        self._load_profile(self._active_profile)
        self._apply_emergency_key()

        if self._app_config.get("first_run", True):
            self.show_settings()
            self._app_config["first_run"] = False
            config.save_app_config(self._app_config)

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    def get_modules(self) -> list[BaseModule]:
        return self._modules

    def pause_all(self) -> None:
        self._paused = True
        for module in self._modules:
            if module.enabled:
                module.disable()
        bus.publish("app.paused")

    def resume_all(self) -> None:
        self._paused = False
        self._load_profile(self._active_profile)
        bus.publish("app.resumed")

    def emergency_stop(self) -> None:
        self.pause_all()
        self._tray.showMessage(
            tr("app.name"),
            tr("app.emergency_stop.message"),
            TrayIcon.MessageIcon.Warning,
            3000,
        )

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    @property
    def active_profile(self) -> str:
        return self._active_profile

    def list_profiles(self) -> list[str]:
        profiles = config.list_profiles()
        return profiles if profiles else ["default"]

    def switch_profile(self, name: str) -> None:
        self._save_current_profile()
        self._active_profile = name
        self._app_config["active_profile"] = name
        config.save_app_config(self._app_config)
        self._load_profile(name)

    def _load_profile(self, name: str) -> None:
        self._profile_data = config.load_profile(name)
        module_settings = self._profile_data.get("modules", {})
        action_assignments = self._profile_data.get("actions", {})

        for module in self._modules:
            settings = module_settings.get(module.MODULE_ID, {})
            module.load_settings(settings)
            if settings.get("enabled", False):
                module.enable()
            else:
                module.disable()

        action_manager.load_from_profile(action_assignments)
        self._apply_emergency_key()

    def _save_current_profile(self) -> None:
        for module in self._modules:
            settings = module.dump_settings()
            settings["enabled"] = module.enabled
            self._profile_data.setdefault("modules", {})[module.MODULE_ID] = settings
        self._profile_data["actions"] = action_manager.dump_for_profile()
        config.save_profile(self._active_profile, self._profile_data)

    # ------------------------------------------------------------------
    # Emergency key
    # ------------------------------------------------------------------

    def _apply_emergency_key(self) -> None:
        key = self._profile_data.get("emergency_key", "F12")
        if key:
            action_manager.assign_trigger("app.emergency_stop", f"Key.{key.lower()}")

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------

    def show_settings(self) -> None:
        if self._settings_window is None:
            from accessmate.gui.main_window import MainWindow
            self._settings_window = MainWindow(self)
        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def quit(self) -> None:
        self._save_current_profile()
        self._qt_app.quit()
