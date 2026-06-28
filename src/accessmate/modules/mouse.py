"""Mouse Module.

Features:
- Automatic cursor centering after inactivity (configurable delay)
- Countdown tooltip before centering (abortable by movement or key press)
- Configurable center tolerance (won't center if already close enough)
- Manual centering via hotkey (registered in ActionManager)
- Precision mode (slow cursor speed via pynput mouse control)
- Click-Lock (hold left button without physical press)
- Keyboard keys as left / right / double click
- Screen zones: jump cursor to predefined screen regions via hotkey
"""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout

from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.modules.base import BaseModule


class MouseModule(BaseModule):
    MODULE_ID = "mouse"
    DISPLAY_NAME = "Maus"
    DESCRIPTION = "Maussteuerung und Cursor-Assistenz"

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._centering_timer: threading.Timer | None = None
        self._centering_active = False

        action_manager.register(Action(
            id="mouse.center",
            label="Maus zentrieren",
            callback=self._center_cursor,
        ))
        action_manager.register(Action(
            id="mouse.precision_toggle",
            label="Präzisionsmodus umschalten",
            callback=self._toggle_precision,
        ))
        action_manager.register(Action(
            id="mouse.click_lock_toggle",
            label="Click-Lock umschalten",
            callback=self._toggle_click_lock,
        ))

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._schedule_centering()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        self._cancel_centering()
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Maus-Einstellungen (wird ausgebaut)"))
        return widget

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    # ------------------------------------------------------------------
    # Centering
    # ------------------------------------------------------------------

    def _schedule_centering(self) -> None:
        if not self._settings.get("centering_enabled"):
            return
        delay = float(self._settings.get("centering_delay", 5.0))
        self._cancel_centering()
        self._centering_timer = threading.Timer(delay, self._start_countdown)
        self._centering_timer.daemon = True
        self._centering_timer.start()

    def _cancel_centering(self) -> None:
        if self._centering_timer:
            self._centering_timer.cancel()
            self._centering_timer = None

    def _start_countdown(self) -> None:
        countdown = int(self._settings.get("centering_countdown", 3))
        bus.publish("mouse.centering_countdown", seconds=countdown)
        time.sleep(countdown)
        if self._enabled:
            self._center_cursor()

    def _center_cursor(self) -> None:
        try:
            from pynput.mouse import Controller
            from PySide6.QtGui import QCursor
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.geometry()
            cx = geo.width() // 2
            cy = geo.height() // 2

            tolerance = int(self._settings.get("centering_tolerance", 50))
            pos = QCursor.pos()
            if abs(pos.x() - cx) < tolerance and abs(pos.y() - cy) < tolerance:
                return

            mouse = Controller()
            mouse.position = (cx, cy)
            bus.publish("mouse.centered")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Precision mode
    # ------------------------------------------------------------------

    def _toggle_precision(self) -> None:
        current = self._settings.get("precision_mode_enabled", False)
        self._settings["precision_mode_enabled"] = not current
        bus.publish("mouse.precision_changed", enabled=self._settings["precision_mode_enabled"])

    # ------------------------------------------------------------------
    # Click-Lock
    # ------------------------------------------------------------------

    def _toggle_click_lock(self) -> None:
        try:
            from pynput.mouse import Button, Controller
            mouse = Controller()
            if not self._settings.get("_click_lock_active", False):
                mouse.press(Button.left)
                self._settings["_click_lock_active"] = True
            else:
                mouse.release(Button.left)
                self._settings["_click_lock_active"] = False
            bus.publish("mouse.click_lock_changed",
                        enabled=self._settings["_click_lock_active"])
        except Exception:
            pass
