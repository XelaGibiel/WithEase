"""Keyboard Module.

Features:
- Key delay: suppress repeated keystrokes within a configurable time window
- Sticky Keys: Shift/Ctrl/Alt/Win – press once to latch, auto-release after
  the next non-modifier key (or press again to unlock manually)
- Modifier status display via event bus (keyboard.modifier_status)
"""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtWidgets import QWidget

from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr
from accessmate.modules.base import BaseModule

from accessmate.core.win_keyboard_hook import (
    MOD_VK,
    inject_modifier_release,
    is_altgr_fake_lctrl,
    shared_keyboard_hook,
    vk_to_combo_str,
)

_MODIFIERS = ("shift", "ctrl", "alt", "win", "altgr")

_VK_RMENU = 0xA5  # right Alt = AltGr on layouts that have it (e.g. German)


class KeyboardModule(BaseModule):
    MODULE_ID = "keyboard"
    DESCRIPTION = "Tastatureingabe-Assistenz und Sticky Keys"

    @property
    def DISPLAY_NAME(self) -> str:  # type: ignore[override]
        return tr("module.keyboard.name")

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._kb_subscribed = False
        self._last_key_time: dict[int, float] = {}
        self._sticky_state: dict[str, bool] = {k: False for k in _MODIFIERS}
        self._pending_release = False  # release latched mods after the current key-up
        # AltGr auto-detection: right-alt only counts as AltGr on layouts that
        # emit the synthetic left-ctrl (scan 0x21D) right before it.
        self._altgr_lctrl_seen = False   # fake left-ctrl just arrived
        self._right_alt_is_altgr = False # last right-alt press was AltGr
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        # Shared low-level hook (pynput's own listener breaks AltGr and, with
        # multiple listeners, starves other hooks).
        shared_keyboard_hook.subscribe(self._on_key_event)
        self._kb_subscribed = True
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        if self._kb_subscribed:
            shared_keyboard_hook.unsubscribe(self._on_key_event)
            self._kb_subscribed = False
        self._release_all_sticky()
        self._last_key_time.clear()
        bus.publish("module.stopped", module_id=self.MODULE_ID)
        bus.publish("keyboard.modifier_status", state=self._sticky_state.copy())

    def get_settings_widget(self) -> QWidget:
        from accessmate.gui.settings.keyboard_settings import KeyboardSettingsWidget
        return KeyboardSettingsWidget(self)

    def on_settings_changed(self) -> None:
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    def load_settings(self, settings: dict[str, Any]) -> None:
        # Release anything latched under the previous settings/profile so no
        # modifier stays held across a profile switch.
        self._release_all_sticky()
        self._settings = settings
        self.on_settings_changed()

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    # ------------------------------------------------------------------
    # Listener callbacks
    # ------------------------------------------------------------------

    def _on_key_event(self, vk: int, scan: int, extended: bool,
                      injected: bool, is_press: bool) -> bool:
        """Shared-hook callback.  Return True to suppress a key.

        Runs in the hook thread; must be quick and never block.
        """
        if injected:
            return False  # our own injected modifier releases

        if is_altgr_fake_lctrl(vk, scan):
            # The synthetic left-ctrl that precedes AltGr.  Its presence is how
            # we know right-alt is acting as AltGr on this layout.
            if is_press:
                self._altgr_lctrl_seen = True
                return False
            self._altgr_lctrl_seen = False
            # While AltGr is latched we keep this left-ctrl held too.
            return bool(self._sticky_state.get("altgr"))

        # Right-alt → AltGr only if the fake left-ctrl preceded it (auto-detect
        # per layout); otherwise it's a plain right-alt.
        if vk == _VK_RMENU:
            if is_press:
                self._right_alt_is_altgr = self._altgr_lctrl_seen
                self._altgr_lctrl_seen = False
            mod = "altgr" if self._right_alt_is_altgr else "alt"
        else:
            mod = MOD_VK.get(vk)

        if is_press:
            return self._handle_press(vk, mod)
        return self._handle_release(vk, mod)

    def _handle_press(self, vk: int, mod: str | None) -> bool:
        # Sticky Keys ---------------------------------------------------
        if mod is not None:
            # Master switch for the whole tool; defaults to True so legacy
            # profiles (which only have the per-modifier flags) keep working.
            sticky_on = self._settings.get("sticky_enabled", True)
            if sticky_on and self._settings.get(f"sticky_{mod}"):
                with self._lock:
                    new_state = not self._sticky_state[mod]
                    self._sticky_state[mod] = new_state
                if new_state:
                    # Latch on: let the physical press through so the OS holds
                    # the modifier down (its release will be suppressed).
                    bus.publish("keyboard.modifier_status",
                                state=self._sticky_state.copy())
                    return False
                # Toggled off (pressed again): release the held modifier and
                # swallow this extra press.
                self._release_modifier(mod)
                bus.publish("keyboard.modifier_status",
                            state=self._sticky_state.copy())
                return True
            return False  # modifier without sticky enabled: pass through

        # Non-modifier key ---------------------------------------------
        # Key delay: suppress rapid repeats of the same key.
        if self._settings.get("delay_enabled"):
            key_str = vk_to_combo_str(vk)
            exceptions = self._settings.get("delay_exceptions", [])
            if key_str is None or key_str not in exceptions:
                delay_ms = int(self._settings.get("delay_ms", 500))
                now = time.monotonic()
                with self._lock:
                    last = self._last_key_time.get(vk, 0.0)
                    if now - last < delay_ms / 1000.0:
                        return True  # swallow the repeat
                    self._last_key_time[vk] = now

        # Defer releasing latched modifiers until this key is *released*.  The
        # character/shortcut is produced on key-down, so the modifier must stay
        # held through the whole key press; releasing now would strip it.
        if self._settings.get("sticky_auto_release", True):
            if any(self._sticky_state.values()):
                self._pending_release = True
        return False

    def _handle_release(self, vk: int, mod: str | None) -> bool:
        if mod is not None:
            with self._lock:
                latched = self._sticky_state.get(mod, False)
            if latched:
                # Swallow the release so Windows keeps the modifier held.
                return True
            return False
        # Non-modifier key released → now release any latched modifiers.
        if self._pending_release:
            self._pending_release = False
            self._release_all_sticky()
        with self._lock:
            self._last_key_time.pop(vk, None)
        return False

    # ------------------------------------------------------------------
    # Sticky helpers
    # ------------------------------------------------------------------

    def _release_modifier(self, name: str) -> None:
        """Inject key-ups for a latched modifier (ignored by our own hook).

        Releases BOTH physical keys of the modifier: the latch may have been
        created with the right-side key, and a left-side key-up (what pynput's
        generic keys send) would leave the right one stuck at OS level.
        """
        inject_modifier_release(name)

    def _release_all_sticky(self) -> None:
        with self._lock:
            active = {k for k, v in self._sticky_state.items() if v}
            self._sticky_state = {k: False for k in self._sticky_state}
        for name in active:
            self._release_modifier(name)
        if active:
            bus.publish("keyboard.modifier_status", state=self._sticky_state.copy())
