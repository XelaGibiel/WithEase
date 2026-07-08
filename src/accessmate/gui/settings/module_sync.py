"""Keeps a module settings page in sync with the module's live state.

When a module is started/stopped outside its own page (emergency stop,
resume, tray actions), the page's enable-checkbox and section states update
immediately instead of showing stale values.
"""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from accessmate.core.event_bus import bus

if TYPE_CHECKING:
    from PySide6.QtWidgets import QCheckBox, QWidget

    from accessmate.modules.base import BaseModule


def sync_module_checkbox(widget: "QWidget", module: "BaseModule",
                         checkbox: "QCheckBox",
                         update_enabled_state: Callable[[bool], None]) -> None:
    """Subscribe the page to the module's start/stop events.

    The checkbox is updated without emitting toggled (which would re-enable/
    re-disable the module).  Unsubscribes automatically when the page widget
    is destroyed (e.g. on a language-change rebuild).
    """

    def on_state(module_id: str, **_: object) -> None:
        if module_id != module.MODULE_ID:
            return
        enabled = module.enabled
        checkbox.blockSignals(True)
        checkbox.setChecked(enabled)
        checkbox.blockSignals(False)
        update_enabled_state(enabled)

    bus.subscribe("module.started", on_state)
    bus.subscribe("module.stopped", on_state)

    def unsubscribe() -> None:
        bus.unsubscribe("module.started", on_state)
        bus.unsubscribe("module.stopped", on_state)

    widget.destroyed.connect(unsubscribe)
