"""Click-Lock cursor indicator – shows 🔒 while Click-Lock is active."""
from __future__ import annotations

from withease.core.event_bus import bus
from withease.gui.widgets.cursor_indicator import CursorIndicator

_SYMBOL = "\U0001F512"  # 🔒


class ClickLockIndicator(CursorIndicator):
    def __init__(self) -> None:
        super().__init__(_SYMBOL, config_key="click_lock")
        bus.subscribe("mouse.click_lock_changed", self._on_state_changed)

    def _on_state_changed(self, enabled: bool, **_: object) -> None:
        if enabled:
            self.show_indicator()
        else:
            self.hide_indicator()
