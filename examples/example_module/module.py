"""Beispielmodul – Minimalvorlage für externe AccessMate-Module.

Zum Ausprobieren diesen Ordner nach %APPDATA%/AccessMate/modules/ kopieren
und AccessMate neu starten – das Modul erscheint als eigene Kategorie in den
Einstellungen.

Was hier gezeigt wird:
- BaseModule-Schnittstelle (start/stop/settings)
- eigene Einstellungsseite mit gespeicherter Checkbox
- eine Aktion im Action-Manager (bekommt damit Hotkey-Zuweisung,
  Konfliktprüfung und Favoriten-Overlay geschenkt)
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.modules.base import BaseModule


class ExampleModule(BaseModule):
    MODULE_ID = "example"
    DISPLAY_NAME = "Beispiel"
    DESCRIPTION = "Minimales Beispielmodul"

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}

        action_manager.register(Action(
            id="example.hello",
            label="Beispiel: Hallo sagen",
            callback=self._say_hello,
        ))

    # -- Lebenszyklus ---------------------------------------------------

    def start(self) -> None:
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    # -- Einstellungen (werden pro Profil gespeichert) --------------------

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    def on_settings_changed(self) -> None:
        action_manager.assign_trigger(
            "example.hello", self._settings.get("hello_hotkey", ""))
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    # -- Einstellungsseite -------------------------------------------------

    def get_settings_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        enabled_cb = QCheckBox("Beispielmodul aktivieren")
        enabled_cb.setChecked(self.enabled)
        enabled_cb.setStyleSheet("font-weight: bold; font-size: 13px;")
        enabled_cb.toggled.connect(
            lambda v: self.enable() if v else self.disable())
        layout.addWidget(enabled_cb)

        layout.addWidget(QLabel(
            "Dies ist ein externes Modul aus\n"
            "%APPDATA%/AccessMate/modules/example_module/"))

        demo_cb = QCheckBox("Eine Einstellung, die im Profil gespeichert wird")
        demo_cb.setChecked(self._settings.get("demo_option", False))

        def on_toggled(value: bool) -> None:
            self._settings["demo_option"] = value
            self.on_settings_changed()

        demo_cb.toggled.connect(on_toggled)
        layout.addWidget(demo_cb)

        from accessmate.gui.widgets.hotkey_edit import HotkeyEdit
        hotkey = HotkeyEdit(self._settings.get("hello_hotkey", ""),
                            action_id="example.hello")

        def on_hotkey(key: str) -> None:
            self._settings["hello_hotkey"] = key
            self.on_settings_changed()

        hotkey.key_changed.connect(on_hotkey)
        layout.addWidget(QLabel("Hotkey für „Hallo sagen“:"))
        layout.addWidget(hotkey)

        layout.addStretch()
        return widget

    # -- Aktion --------------------------------------------------------------

    def _say_hello(self) -> None:
        bus.publish("example.hello_fired")
