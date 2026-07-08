"""Main application controller.

Owns the module registry, profile management, and the emergency stop.
The GUI and tray are created here and live for the entire app lifetime.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from accessmate.core import config
from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.core import i18n
from accessmate.core.i18n import tr
from accessmate.core.win_keyboard_hook import (
    current_combo_str,
    shared_keyboard_hook,
)
from accessmate.modules.base import BaseModule


from accessmate.modules.keyboard import KeyboardModule
from accessmate.modules.macros import MacrosModule
from accessmate.modules.mouse import MouseModule
from accessmate.tray import TrayIcon


class _EmergencyBridge(QObject):
    """Marshals the emergency hotkey from the hook thread to the main thread."""
    triggered = Signal()


class AccessMateApp:
    def __init__(self, qt_app: QApplication) -> None:
        self._qt_app = qt_app
        self._app_config = config.load_app_config()
        i18n.load(self._app_config.get("language", "de"))
        from accessmate.gui.theme import apply_theme
        apply_theme(qt_app, self._app_config.get("theme", "system"),
                    self._app_config.get("contrast", "normal"),
                    int(self._app_config.get("font_size", 0)))
        self._active_profile: str = self._app_config.get("active_profile", "default")
        self._profile_data: dict[str, Any] = {}
        self._paused = False
        self._pre_pause_state: dict[str, bool] = {}
        self._settings_window = None

        self._modules: list[BaseModule] = [
            MouseModule(),
            KeyboardModule(),
            MacrosModule(),
        ]

        # External (third-party) modules from %APPDATA%/AccessMate/modules/.
        # Each gets its own settings category automatically; a broken module
        # is skipped by the loader and can never prevent the app start.
        from accessmate.core.module_loader import discover_external_modules
        for ext in discover_external_modules():
            if any(m.MODULE_ID == ext.MODULE_ID for m in self._modules):
                import logging
                logging.getLogger(__name__).warning(
                    "external module %r ignored: MODULE_ID %r already exists",
                    type(ext).__name__, ext.MODULE_ID)
                continue
            self._modules.append(ext)

        # The emergency key must work even when no module is enabled, so the
        # app itself listens on the shared hook (modules come and go).  Both
        # trigger paths (own hook + ActionManager fire from a module's hook
        # thread) emit the bridge signal, so emergency_stop always runs on the
        # Qt main thread; the pause guard makes double-firing harmless.
        self._emergency_trigger = ""
        self._emergency_bridge = _EmergencyBridge()
        self._emergency_bridge.triggered.connect(self.toggle_emergency)
        shared_keyboard_hook.subscribe(self._on_emergency_key)

        # Registered only so the hotkey shows up in conflict checks; the key
        # itself is handled exclusively by _on_emergency_key above.  A firing
        # callback here would double-trigger (module hook + app hook) and make
        # the stop/resume toggle flip twice per key press.
        action_manager.register(Action(
            id="app.emergency_stop",
            label=tr("app.emergency_stop"),
            callback=lambda: None,
        ))

        self._tray = TrayIcon(self)
        self._tray.show()

        from accessmate.gui.widgets.click_lock_indicator import ClickLockIndicator
        from accessmate.gui.widgets.cursor_indicator import (
            CenteringIndicator,
            PrecisionIndicator,
        )
        from accessmate.gui.widgets.modifier_indicator import ModifierIndicator
        from accessmate.gui.widgets.macro_indicator import MacroModeIndicator
        from accessmate.gui.widgets.cursor_highlight_overlay import CursorHighlightOverlay
        self._click_lock_indicator = ClickLockIndicator()
        self._centering_indicator = CenteringIndicator()
        self._precision_indicator = PrecisionIndicator()
        self._modifier_indicator = ModifierIndicator()
        self._macro_indicator = MacroModeIndicator()
        self._cursor_highlight = CursorHighlightOverlay()
        from accessmate.gui.widgets.actions_overlay import ActionsOverlay
        self._actions_overlay = ActionsOverlay(self)

        self._load_profile(self._active_profile)
        self._apply_emergency_key()
        self._apply_indicator_positions()

        # Subscribe AFTER loading so the initial load never overwrites the profile.
        # During load, on_settings_changed fires before enable() is called, which
        # would save enabled=False over the correct value in the file.
        bus.subscribe("module.settings_changed", lambda **_: self._save_current_profile())
        bus.subscribe("module.started",          lambda **_: self._save_current_profile())
        bus.subscribe("module.stopped",          lambda **_: self._save_current_profile())

        if self._app_config.get("first_run", True):
            self.show_settings()
            self._app_config["first_run"] = False
            config.save_app_config(self._app_config)

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    def get_modules(self) -> list[BaseModule]:
        return self._modules

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause_all(self) -> None:
        if self._paused:
            return  # already paused – don't overwrite the pre-pause state
        # Remember which modules were active before pausing
        self._pre_pause_state = {m.MODULE_ID: m.enabled for m in self._modules}
        self._paused = True
        for module in self._modules:
            if module.enabled:
                module.disable()
        bus.publish("app.paused")

    def resume_all(self) -> None:
        self._paused = False
        # Restore exactly the modules that were active before pausing
        for module in self._modules:
            if self._pre_pause_state.get(module.MODULE_ID, False):
                module.enable()
        self._pre_pause_state = {}
        bus.publish("app.resumed")

    def toggle_emergency(self) -> None:
        """Emergency key/button toggles: first press stops, next press resumes."""
        import logging
        import time
        # Debounce: key auto-repeat or duplicated events must not flip the
        # toggle right back within the same key press.
        now = time.monotonic()
        if now - getattr(self, "_last_emergency_toggle", 0.0) < 0.5:
            return
        self._last_emergency_toggle = now
        logging.getLogger(__name__).info(
            "emergency toggle: %s", "resume" if self._paused else "stop")
        if self._paused:
            self.resume_all()
            self._tray.showMessage(
                tr("app.name"),
                tr("app.emergency_stop.resumed"),
                TrayIcon.MessageIcon.Information,
                3000,
            )
        else:
            self.emergency_stop()

    def emergency_stop(self) -> None:
        # Rescue any stuck modifiers first (e.g. a latched sticky key whose
        # release got lost) – the panic button must restore a usable keyboard.
        from accessmate.core.win_keyboard_hook import release_all_modifiers
        release_all_modifiers()
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

    @staticmethod
    def _valid_profile_name(name: str) -> bool:
        name = name.strip()
        return bool(name) and not any(c in name for c in '\\/:*?"<>|')

    def switch_profile(self, name: str) -> None:
        self._save_current_profile()
        self._active_profile = name
        self._app_config["active_profile"] = name
        config.save_app_config(self._app_config)
        self._load_profile(name)
        bus.publish("profiles.changed", switched=True)

    def create_profile(self, name: str) -> bool:
        """Create a new profile (with defaults) and switch to it."""
        name = name.strip()
        if not self._valid_profile_name(name) or name in self.list_profiles():
            return False
        config.load_profile(name)  # creates the file with defaults
        self.switch_profile(name)
        return True

    def rename_profile(self, old: str, new: str) -> bool:
        new = new.strip()
        if (not self._valid_profile_name(new) or new in self.list_profiles()
                or old not in self.list_profiles()):
            return False
        if old == self._active_profile:
            self._active_profile = new
            self._app_config["active_profile"] = new
            config.save_app_config(self._app_config)
            self._profile_data["name"] = new
            self._save_current_profile()      # writes under the new name
            config.delete_profile(old)
        else:
            data = config.load_profile(old)
            data["name"] = new
            config.save_profile(new, data)
            config.delete_profile(old)
        bus.publish("profiles.changed", switched=False)
        return True

    def delete_profile(self, name: str) -> bool:
        """Delete a profile.  The active profile cannot be deleted."""
        if name == self._active_profile or name not in self.list_profiles():
            return False
        config.delete_profile(name)
        bus.publish("profiles.changed", switched=False)
        return True

    def _load_profile(self, name: str) -> None:
        # While loading, modules publish settings/started/stopped events whose
        # save-subscribers would write a half-loaded mix of the OLD profile's
        # module settings into the NEW profile file.  Block saving meanwhile.
        self._loading_profile = True
        try:
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
        finally:
            self._loading_profile = False

    def _save_current_profile(self) -> None:
        import logging
        if getattr(self, "_loading_profile", False):
            return  # mid-load events must not overwrite the new profile
        try:
            for module in self._modules:
                settings = module.dump_settings()
                settings["enabled"] = module.enabled
                self._profile_data.setdefault("modules", {})[module.MODULE_ID] = settings
            self._profile_data["actions"] = action_manager.dump_for_profile()
            config.save_profile(self._active_profile, self._profile_data)
            logging.getLogger(__name__).info(
                "profile %r saved", self._active_profile)
            bus.publish("profile.saved", name=self._active_profile)
        except Exception:
            logging.getLogger(__name__).exception("saving profile failed")
            raise

    # ------------------------------------------------------------------
    # Favourites / actions overlay
    # ------------------------------------------------------------------

    def get_favorites(self) -> list[str]:
        """Favourite ids: action ids, or 'macro:<id>' for macros."""
        return self._profile_data.setdefault("favorites", [])

    def is_favorite(self, fid: str) -> bool:
        return fid in self.get_favorites()

    def toggle_favorite(self, fid: str) -> None:
        favs = self.get_favorites()
        if fid in favs:
            favs.remove(fid)
        else:
            favs.append(fid)
        self._save_current_profile()
        bus.publish("overlay.config_changed")

    def move_favorite(self, fid: str, delta: int) -> None:
        """Move a favourite up/down in the list (order = overlay order)."""
        favs = self.get_favorites()
        if fid not in favs:
            return
        i = favs.index(fid)
        j = i + delta
        if 0 <= j < len(favs):
            favs[i], favs[j] = favs[j], favs[i]
            self._save_current_profile()
            bus.publish("overlay.config_changed")

    def get_favorite_rows(self) -> list[tuple[str, str]]:
        """(label, formatted hotkey) for each favourite, for the overlay."""
        from accessmate.gui.widgets.hotkey_edit import HotkeyEdit
        actions = {a.id: a for a in action_manager.get_all()}
        macros = {}
        for module in self._modules:
            if module.MODULE_ID == "macros":
                macros = {m.id: m for m in getattr(module, "_macros", [])}
        rows: list[tuple[str, str]] = []
        for fid in self.get_favorites():
            if fid == "macros.trigger":
                for module in self._modules:
                    if module.MODULE_ID == "macros":
                        trigger = module._settings.get("trigger_key", "")
                        key = HotkeyEdit._format_key(trigger) if trigger else "—"
                        rows.append((tr("module.macros.trigger_key"), key))
                continue
            if fid.startswith("macro:"):
                macro = macros.get(fid[6:])
                if macro:
                    key = (HotkeyEdit._format_key(macro.trigger_key)
                           if macro.trigger_key else "—")
                    rows.append((macro.label, key))
            else:
                action = actions.get(fid)
                if action:
                    key = (HotkeyEdit._format_key(action.trigger)
                           if action.trigger else "—")
                    rows.append((action.label, key))
        return rows

    def get_overlay_config(self) -> dict:
        return self._profile_data.setdefault("overlay", {
            "enabled": False,
            "position": "bottom-right",
            "hover_hide": False,
            "custom_pos": None,
        })

    def set_overlay_option(self, key: str, value) -> None:
        self.get_overlay_config()[key] = value
        self._save_current_profile()
        bus.publish("overlay.config_changed")

    def set_overlay_custom_pos(self, x: int, y: int) -> None:
        cfg = self.get_overlay_config()
        cfg["custom_pos"] = [x, y]
        cfg["position"] = "custom"
        self._save_current_profile()
        bus.publish("overlay.config_changed")

    # ------------------------------------------------------------------
    # Emergency key
    # ------------------------------------------------------------------

    def _apply_indicator_positions(self) -> None:
        kb_settings = self._profile_data.get("modules", {}).get("keyboard", {})
        pos = kb_settings.get("sticky_indicator_position", "bottom-right")
        self._modifier_indicator.set_position(pos)
        size = kb_settings.get("sticky_chip_size", 24)
        self._modifier_indicator.set_chip_size(size)

        macro_settings = self._profile_data.get("modules", {}).get("macros", {})
        self._macro_indicator.set_chip_size(macro_settings.get("chip_size", 28))

    def _apply_emergency_key(self) -> None:
        key = self._profile_data.get("emergency_key", "F12")
        if (key and "+" not in key
                and not (key.startswith("Key.") or key.startswith("'"))):
            # Legacy profiles store just "F12" – convert to hotkey format.
            # Combos ("ctrl+shift+Key.home") are already in hotkey format.
            key = f"Key.{key.lower()}"
        self._emergency_trigger = key or ""
        action_manager.assign_trigger("app.emergency_stop", self._emergency_trigger)

    def _on_emergency_key(self, vk: int, scan: int, extended: bool,
                          injected: bool, is_press: bool) -> bool:
        """Shared-hook callback (hook thread) – never suppresses."""
        if (is_press and not injected and self._emergency_trigger
                and current_combo_str(vk) == self._emergency_trigger):
            self._emergency_bridge.triggered.emit()
        return False

    def set_emergency_key(self, key: str) -> None:
        """Called from the settings UI with a pynput-format key string."""
        self._profile_data["emergency_key"] = key
        self._apply_emergency_key()
        self._save_current_profile()

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
