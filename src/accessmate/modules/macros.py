"""Macros Module.

Workflow
--------
1. User presses the trigger combo (e.g. Ctrl+M) → macro mode activates.
2. AccessMate waits for one more key press (the macro key).
3. That key executes the assigned macro action.
4. Macro mode ends automatically (or on Escape).

Listener design
---------------
Two separate pynput listeners are used:

GlobalHotKeys  – always running while the module is enabled.
               Detects the trigger combo and activates macro mode.
               Does NOT use suppress=True, so normal typing is unaffected.
               Side-effect: the trigger key itself reaches the active app.
               (Mitigate by choosing a trigger that produces no visible
               output, such as F9, Scroll Lock, etc.)

Capture listener (one-shot, suppress=True)
               Starts when macro mode activates.
               Captures exactly ONE non-modifier key press, then stops
               itself (returns False from on_press).
               The captured key and any held modifiers are matched against
               the macro list.
               Because the listener stops before _execute runs, there is
               no active suppress=True hook while KeyController.type()
               injects characters, so the text reaches the app normally.
"""
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

from PySide6.QtWidgets import QWidget

from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr
from accessmate.core.win_keyboard_hook import (
    MOD_VK as _MOD_VK,
    NUMPAD_VK as _NUMPAD_VK,
    effective_modifiers,
    is_altgr_fake_lctrl,
    shared_keyboard_hook,
    vk_to_combo_str as _vk_to_combo_str,
)
from accessmate.modules.base import BaseModule

try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
    from pynput.keyboard import Controller as KeyController
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False


MacroType = Literal["text", "keys", "mouse", "app"]

# VK→combo-string conversion and modifier maps live in core.win_keyboard_hook
# (imported above) so the mouse module can share them.  We drive macros from a
# raw low-level hook instead of pynput's listener because pynput's character
# translation breaks AltGr/dead keys in the foreground app.


def _str_to_pynput_key(part: str) -> Any:
    """Convert a pynput-format key string to a pynput Key or KeyCode."""
    if not PYNPUT_AVAILABLE:
        return None
    part = part.strip()
    _mods = {
        "ctrl": pynput_keyboard.Key.ctrl,
        "alt":  pynput_keyboard.Key.alt,
        "shift": pynput_keyboard.Key.shift,
        "win":  pynput_keyboard.Key.cmd,
    }
    if part in _mods:
        return _mods[part]
    if part.startswith("Key.num_"):
        vk = _NUMPAD_VK.get(part[4:])
        if vk is not None:
            return pynput_keyboard.KeyCode.from_vk(vk)
    if part.startswith("Key."):
        return getattr(pynput_keyboard.Key, part[4:], None)
    if part.startswith("'") and part.endswith("'") and len(part) >= 3:
        return pynput_keyboard.KeyCode.from_char(part[1:-1])
    if len(part) == 1:
        return pynput_keyboard.KeyCode.from_char(part)
    return None


@dataclass
class Macro:
    id: str
    label: str
    trigger_key: str
    type: MacroType
    payload: dict[str, Any] = field(default_factory=dict)


class MacrosModule(BaseModule):
    MODULE_ID = "macros"
    DESCRIPTION = "Tastenkürzel für Textbausteine, Programme und Aktionen"

    @property
    def DISPLAY_NAME(self) -> str:  # type: ignore[override]
        return tr("module.macros.name")

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._macros: list[Macro] = []
        self._macro_mode = False
        self._kb_subscribed = False
        self._trigger_mods: frozenset[str] = frozenset()
        self._trigger_main: str = ""

        action_manager.register(Action(
            id="macros.toggle_mode",
            label="Makromodus umschalten",
            callback=self._cancel_macro_mode,
        ))

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._refresh_trigger()
        self._start_hook()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        self._stop_hook()
        self._macro_mode = False
        bus.publish("macros.mode_changed", active=False)
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        from accessmate.gui.settings.macros_settings import MacrosSettingsWidget
        return MacrosSettingsWidget(self)

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._macros = [Macro(**m) for m in settings.get("macros", [])]

    def dump_settings(self) -> dict[str, Any]:
        return {
            **self._settings,
            "macros": [vars(m) for m in self._macros],
        }

    def on_settings_changed(self) -> None:
        self._refresh_trigger()
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    # ------------------------------------------------------------------
    # Low-level hook – trigger detection + one-shot macro key capture
    # ------------------------------------------------------------------

    def _refresh_trigger(self) -> None:
        parts = self._settings.get("trigger_key", "").split("+")
        self._trigger_main = parts[-1] if parts and parts[-1] else ""
        self._trigger_mods = frozenset(parts[:-1])

    def _start_hook(self) -> None:
        if not self._kb_subscribed:
            shared_keyboard_hook.subscribe(self._on_key_event)
            self._kb_subscribed = True

    def _stop_hook(self) -> None:
        if self._kb_subscribed:
            shared_keyboard_hook.unsubscribe(self._on_key_event)
            self._kb_subscribed = False

    def _on_key_event(self, vk: int, scan: int, extended: bool,
                      injected: bool, is_press: bool) -> bool:
        """Raw hook callback.  Return True to suppress the key.

        Runs in the hook thread's message loop – must return quickly and never
        block (macro execution is deferred via a Timer).
        """
        if injected:
            return False  # ignore our own synthetic keys

        # Ignore the synthetic left-ctrl that AltGr generates, so AltGr is
        # never treated as a Ctrl modifier (would corrupt trigger matching).
        if is_altgr_fake_lctrl(vk, scan):
            return False

        if _MOD_VK.get(vk):
            return False  # modifiers pass through; state is read from the OS

        if not is_press:
            return False

        key_str = _vk_to_combo_str(vk)

        if self._macro_mode:
            return self._handle_capture(key_str)
        if key_str is not None:
            return self._handle_trigger(key_str)
        return False

    @staticmethod
    def _effective_mods() -> frozenset[str]:
        """OS-level held modifiers (includes Sticky-held ones)."""
        return effective_modifiers()

    def _handle_trigger(self, key_str: str) -> bool:
        if not self._trigger_main:
            return False
        if key_str == self._trigger_main and self._effective_mods() == self._trigger_mods:
            self._macro_mode = True
            bus.publish("macros.mode_changed", active=True)
            return True  # swallow the trigger key so it produces no output
        return False

    def _handle_capture(self, key_str: str | None) -> bool:
        # Leave macro mode – the next key is the macro key (or a cancel).
        self._macro_mode = False
        bus.publish("macros.mode_changed", active=False)

        if key_str is not None:
            sorted_mods = sorted(self._effective_mods())
            combo = "+".join(sorted_mods + [key_str]) if sorted_mods else key_str
            for macro in self._macros:
                if macro.trigger_key == combo:
                    # Defer so we return from the hook promptly; the injected
                    # keys then won't be seen as physical input.
                    threading.Timer(0.1, self._execute, args=[macro]).start()
                    break
        # Always swallow the macro key so it doesn't leak into the app.
        return True

    def _cancel_macro_mode(self) -> None:
        if self._macro_mode:
            self._macro_mode = False
            bus.publish("macros.mode_changed", active=False)

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
            elif macro.type == "mouse":
                self._run_sequence(macro.payload.get("steps", []))
            bus.publish("macros.executed", macro_id=macro.id)
        except Exception as e:
            bus.publish("macros.error", macro_id=macro.id, error=str(e))

    def _type_text(self, text: str) -> None:
        if not PYNPUT_AVAILABLE or not text:
            return
        KeyController().type(text)

    def _send_keys(self, combination: str) -> None:
        if not PYNPUT_AVAILABLE or not combination:
            return
        parts = [p.strip() for p in combination.split("+")]
        ctrl = KeyController()
        pressed = []
        try:
            for part in parts[:-1]:
                key = _str_to_pynput_key(part)
                if key is not None:
                    ctrl.press(key)
                    pressed.append(key)
            main = _str_to_pynput_key(parts[-1])
            if main is not None:
                ctrl.press(main)
                ctrl.release(main)
        finally:
            # Always release held modifiers, even if a press above raised –
            # otherwise they stay down at OS level (stuck-key symptom).
            for key in reversed(pressed):
                try:
                    ctrl.release(key)
                except Exception:
                    pass

    def _run_sequence(self, steps: list[dict[str, Any]]) -> None:
        """Execute a step sequence (mouse clicks, text, key combos, waits).

        Runs on the deferred Timer thread, so sleeping here is fine.  Injected
        input is ignored by our own hook (injected flag), so the sequence never
        re-triggers macros.
        """
        if not PYNPUT_AVAILABLE:
            return
        import time as _time
        mouse = pynput_mouse.Controller()
        for step in steps:
            kind = step.get("type", "")
            if kind == "mouse":
                pos = step.get("pos")
                if pos:
                    mouse.position = (int(pos[0]), int(pos[1]))
                    _time.sleep(0.05)  # let the cursor settle before clicking
                action = step.get("action", "left")
                button = (pynput_mouse.Button.right if action == "right"
                          else pynput_mouse.Button.left)
                mouse.click(button, 2 if action == "double" else 1)
            elif kind == "text":
                KeyController().type(step.get("text", ""))
            elif kind == "keys":
                self._send_keys(step.get("combination", ""))
            elif kind == "wait":
                _time.sleep(max(0, int(step.get("ms", 0))) / 1000.0)
            elif kind == "window":
                self._activate_window(step.get("title", ""))
                _time.sleep(0.15)  # give the window time to come forward
            _time.sleep(0.05)  # small gap between steps for reliability

    @staticmethod
    def _activate_window(title: str) -> None:
        """Bring the first visible window whose title contains `title` to the
        foreground (case-insensitive substring match)."""
        if not title:
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        needle = title.lower()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if needle in buf.value.lower():
                        found.append(hwnd)
                        return False  # stop enumeration
            return True

        user32.EnumWindows(enum_proc, 0)
        if not found:
            return
        hwnd = found[0]
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        # Windows blocks SetForegroundWindow from background processes.
        # A synthetic Alt press/release lifts that restriction (same trick
        # AutoHotkey uses via its WinActivate implementation).
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x02
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)

    def _launch_app(self, path: str, args: list[str]) -> None:
        if not path:
            return
        if path.lower().endswith(".lnk"):
            # Shortcuts can't be exec'd directly – let the shell resolve them.
            # (args are ignored for .lnk; the shortcut carries its own.)
            import os
            os.startfile(path)  # noqa: S606 – deliberate shell open
            return
        subprocess.Popen([path] + args)
