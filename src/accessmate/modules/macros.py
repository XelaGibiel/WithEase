"""Macros Module.

Workflow:
1. User activates macro mode (via configurable trigger key)
2. AccessMate waits for a second key press
3. That key executes the assigned macro action
4. Macro mode ends automatically after execution

Supported macro types:
- text     : type a text snippet
- keys     : send a key combination (e.g. Ctrl+C)
- mouse    : mouse action (left/right/double click at optional coordinates)
- app      : launch an application or script
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Literal

from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout

from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.modules.base import BaseModule

try:
    from pynput import keyboard as pynput_keyboard
    from pynput.keyboard import Controller as KeyController
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False


MacroType = Literal["text", "keys", "mouse", "app"]


@dataclass
class Macro:
    id: str
    label: str
    trigger_key: str
    type: MacroType
    payload: dict[str, Any] = field(default_factory=dict)


class MacrosModule(BaseModule):
    MODULE_ID = "macros"
    DISPLAY_NAME = "Makros"
    DESCRIPTION = "Tastenkürzel für Textbausteine, Programme und Aktionen"

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._macros: list[Macro] = []
        self._macro_mode = False
        self._listener: Any = None

        action_manager.register(Action(
            id="macros.toggle_mode",
            label="Makromodus umschalten",
            callback=self._toggle_macro_mode,
        ))

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not PYNPUT_AVAILABLE:
            return
        self._listener = pynput_keyboard.Listener(on_press=self._on_press)
        self._listener.start()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._macro_mode = False
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Makro-Einstellungen (wird ausgebaut)"))
        return widget

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._macros = [
            Macro(**m) for m in settings.get("macros", [])
        ]

    def dump_settings(self) -> dict[str, Any]:
        return {
            **self._settings,
            "macros": [vars(m) for m in self._macros],
        }

    # ------------------------------------------------------------------
    # Macro mode
    # ------------------------------------------------------------------

    def _toggle_macro_mode(self) -> None:
        self._macro_mode = not self._macro_mode
        bus.publish("macros.mode_changed", active=self._macro_mode)

    def _on_press(self, key: Any) -> None:
        if not self._macro_mode:
            return
        key_str = str(key)
        for macro in self._macros:
            if macro.trigger_key == key_str:
                self._execute(macro)
                self._macro_mode = False
                bus.publish("macros.mode_changed", active=False)
                return False  # consume key

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute(self, macro: Macro) -> None:
        try:
            if macro.type == "text":
                self._type_text(macro.payload.get("text", ""))
            elif macro.type == "keys":
                self._send_keys(macro.payload.get("combination", ""))
            elif macro.type == "app":
                self._launch_app(macro.payload.get("path", ""),
                                 macro.payload.get("args", []))
            bus.publish("macros.executed", macro_id=macro.id)
        except Exception as e:
            bus.publish("macros.error", macro_id=macro.id, error=str(e))

    def _type_text(self, text: str) -> None:
        if not PYNPUT_AVAILABLE or not text:
            return
        ctrl = KeyController()
        ctrl.type(text)

    def _send_keys(self, combination: str) -> None:
        if not PYNPUT_AVAILABLE or not combination:
            return
        # e.g. "ctrl+c" -> press ctrl, press c, release c, release ctrl
        ctrl = KeyController()
        parts = [p.strip().lower() for p in combination.split("+")]
        modifiers = []
        for part in parts[:-1]:
            mod = getattr(pynput_keyboard.Key, part, None)
            if mod:
                ctrl.press(mod)
                modifiers.append(mod)
        last = parts[-1]
        key = getattr(pynput_keyboard.Key, last, last)
        ctrl.press(key)
        ctrl.release(key)
        for mod in reversed(modifiers):
            ctrl.release(mod)

    def _launch_app(self, path: str, args: list[str]) -> None:
        if not path:
            return
        subprocess.Popen([path] + args)
