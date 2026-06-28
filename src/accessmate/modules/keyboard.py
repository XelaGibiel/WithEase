"""Keyboard Module.

Features:
- Key delay: suppress repeated keystrokes if a key is held down
  (configurable delay, per-key exception list)
- Sticky Keys: Shift, Ctrl, Alt, Win – press once, stays active until
  next non-modifier key. Auto-release configurable.
- Modifier status display via event bus (GUI can show an overlay)
"""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout

from accessmate.core.event_bus import bus
from accessmate.modules.base import BaseModule

try:
    from pynput import keyboard as pynput_keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False


MODIFIER_KEYS = {
    "shift": {pynput_keyboard.Key.shift, pynput_keyboard.Key.shift_r} if PYNPUT_AVAILABLE else set(),
    "ctrl": {pynput_keyboard.Key.ctrl, pynput_keyboard.Key.ctrl_r} if PYNPUT_AVAILABLE else set(),
    "alt": {pynput_keyboard.Key.alt, pynput_keyboard.Key.alt_r} if PYNPUT_AVAILABLE else set(),
    "win": {pynput_keyboard.Key.cmd, pynput_keyboard.Key.cmd_r} if PYNPUT_AVAILABLE else set(),
}


class KeyboardModule(BaseModule):
    MODULE_ID = "keyboard"
    DISPLAY_NAME = "Tastatur"
    DESCRIPTION = "Tastatureingabe-Assistenz und Sticky Keys"

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._listener: Any = None
        self._last_key_time: dict[str, float] = {}
        self._sticky_state: dict[str, bool] = {k: False for k in MODIFIER_KEYS}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not PYNPUT_AVAILABLE:
            return
        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._last_key_time.clear()
        self._sticky_state = {k: False for k in MODIFIER_KEYS}
        bus.publish("module.stopped", module_id=self.MODULE_ID)
        bus.publish("keyboard.modifier_status", state=self._sticky_state.copy())

    def get_settings_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Tastatur-Einstellungen (wird ausgebaut)"))
        return widget

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    # ------------------------------------------------------------------
    # Listener callbacks
    # ------------------------------------------------------------------

    def _on_press(self, key: Any) -> None:
        key_str = str(key)

        # --- Key delay ---
        if self._settings.get("delay_enabled"):
            exceptions = self._settings.get("delay_exceptions", [])
            if key_str not in exceptions:
                delay_ms = int(self._settings.get("delay_ms", 500))
                now = time.monotonic()
                with self._lock:
                    last = self._last_key_time.get(key_str, 0.0)
                    if now - last < delay_ms / 1000.0:
                        return False  # suppress repeat
                    self._last_key_time[key_str] = now

        # --- Sticky Keys ---
        for name, keys in MODIFIER_KEYS.items():
            if key in keys and self._settings.get(f"sticky_{name}"):
                with self._lock:
                    self._sticky_state[name] = not self._sticky_state[name]
                bus.publish("keyboard.modifier_status", state=self._sticky_state.copy())
                return

        # Non-modifier key pressed – auto-release all active sticky modifiers
        if self._settings.get("sticky_auto_release"):
            with self._lock:
                changed = any(self._sticky_state.values())
                self._sticky_state = {k: False for k in self._sticky_state}
            if changed:
                bus.publish("keyboard.modifier_status", state=self._sticky_state.copy())

    def _on_release(self, key: Any) -> None:
        key_str = str(key)
        with self._lock:
            self._last_key_time.pop(key_str, None)
