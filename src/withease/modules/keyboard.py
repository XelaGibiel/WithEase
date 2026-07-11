"""Keyboard Module.

Features:
- Key delay: suppress repeated keystrokes within a configurable time window
- Sticky Keys: Shift/Ctrl/Alt/Win – TAP a modifier (press and release without
  using it) to latch it; it then applies to the next key and auto-releases.
  Holding a modifier together with another key (e.g. Shift+U for a capital U)
  behaves completely normally and does NOT latch – so nothing toggles while
  the key is held down.  Tapping a latched modifier again releases it.
- Modifier status display via event bus (keyboard.modifier_status)
"""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtWidgets import QWidget

from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.modules.base import BaseModule

from withease.core.keyboard_hook import (
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
        # Per-modifier physical tracking, used to decide latching on RELEASE:
        #   _mod_down – the modifier key is physically held right now
        #   _mod_used – another key was pressed while it was held (classic use)
        self._mod_down: dict[str, bool] = {k: False for k in _MODIFIERS}
        self._mod_used: dict[str, bool] = {k: False for k in _MODIFIERS}
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
        from withease.gui.settings.keyboard_settings import KeyboardSettingsWidget
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
            sticky_on = (self._settings.get("sticky_enabled", True)
                         and bool(self._settings.get(f"sticky_{mod}")))
            with self._lock:
                self._mod_down[mod] = True
                latched = self._sticky_state[mod]
            if not sticky_on:
                return False  # sticky not enabled for this modifier
            if latched:
                # Already latched → the OS already holds this modifier; swallow
                # the extra physical press so nothing double-fires.  Repeated
                # auto-repeat presses land here too, so holding never toggles.
                return True
            # Not latched: let the press through so the OS holds the modifier
            # while it is physically down.  Whether it LATCHES is decided on
            # release (a clean tap latches; holding + using does not).
            return False

        # Non-modifier key ---------------------------------------------
        # Any modifier physically held while this key is pressed counts as
        # classic use → that modifier will NOT latch when it is released.
        with self._lock:
            for m in _MODIFIERS:
                if self._mod_down[m]:
                    self._mod_used[m] = True

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
            with self._lock:
                if any(self._sticky_state.values()):
                    self._pending_release = True
        return False

    def _handle_release(self, vk: int, mod: str | None) -> bool:
        if mod is not None:
            sticky_on = (self._settings.get("sticky_enabled", True)
                         and bool(self._settings.get(f"sticky_{mod}")))
            with self._lock:
                was_down = self._mod_down[mod]
                self._mod_down[mod] = False
                used = self._mod_used[mod]
                self._mod_used[mod] = False
                latched = self._sticky_state[mod]
            if not sticky_on:
                return False  # normal release
            if latched:
                # Tapping a latched modifier again releases it.
                with self._lock:
                    self._sticky_state[mod] = False
                self._release_modifier(mod)
                bus.publish("keyboard.modifier_status",
                            state=self._sticky_state.copy())
                return True  # swallow the physical release
            # Not latched: latch only on a clean TAP – pressed and released
            # without being used together with another key.  Holding e.g.
            # Shift+U to type a capital letter therefore behaves normally.
            if was_down and not used:
                with self._lock:
                    self._sticky_state[mod] = True
                bus.publish("keyboard.modifier_status",
                            state=self._sticky_state.copy())
                return True  # swallow release → OS keeps the modifier held
            return False  # used classically → normal release

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
            self._mod_used = {k: False for k in self._mod_used}
        for name in active:
            self._release_modifier(name)
        if active:
            bus.publish("keyboard.modifier_status", state=self._sticky_state.copy())
