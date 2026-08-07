"""KeyListEdit – pick exception keys as toggleable "key caps".

The common editing/navigation keys are offered as one-tap toggles (highlighted
when active); anything else is added via "other key" recording and shown as a
removable cap.

    widget = KeyListEdit(current_keys=["Key.space", "Key.enter"])
    widget.keys_changed.connect(lambda keys: print(keys))
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from withease.core.i18n import tr

# The keys people almost always want as exceptions, offered as quick toggles.
# (pynput key string, optional fixed label – None = use the localised name.)
_PRESETS: list[tuple[str, str | None]] = [
    ("Key.left", "←"),
    ("Key.up", "↑"),
    ("Key.down", "↓"),
    ("Key.right", "→"),
    ("Key.backspace", None),
    ("Key.delete", None),
    ("Key.space", None),
    ("Key.enter", None),
]
_PRESET_KEYS = {k for k, _ in _PRESETS}

_CAP_STYLE = """
QPushButton {
    border: 0.5px solid palette(mid);
    border-bottom: 2px solid palette(mid);
    border-radius: 7px;
    padding: 5px 12px;
    background: palette(base);
}
QPushButton:hover { border-color: palette(highlight); }
QPushButton:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-color: palette(highlight);
}
"""


class _KeyChip(QWidget):
    """A single removable key cap (used for custom, non-preset keys)."""
    removed = Signal(str)

    def __init__(self, key: str, label: str) -> None:
        super().__init__()
        self._key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(QLabel(label))

        from withease.gui.ui_utils import em
        btn = QPushButton("✕")
        btn.setFixedSize(em(1.1), em(1.1))
        btn.setFlat(True)
        btn.setStyleSheet("color: palette(mid);")
        btn.clicked.connect(lambda: self.removed.emit(self._key))
        layout.addWidget(btn)

        self.setStyleSheet(
            "QWidget { background: palette(button); border: 0.5px solid"
            " palette(mid); border-radius: 8px; }")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class KeyListEdit(QWidget):
    """Pick exception keys via preset toggles + custom recording."""

    keys_changed = Signal(list)  # emits list[str] of pynput key strings

    def __init__(self, current_keys: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keys: list[str] = list(current_keys or [])
        self._recording = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Preset quick-toggles (4 per row).
        self._preset_area = QWidget()
        preset_grid = QGridLayout(self._preset_area)
        preset_grid.setContentsMargins(0, 0, 0, 0)
        preset_grid.setSpacing(6)
        self._preset_btns: dict[str, QPushButton] = {}
        for i, (key, fixed) in enumerate(_PRESETS):
            btn = QPushButton(fixed or self._format_key(key))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_CAP_STYLE)
            btn.toggled.connect(lambda on, k=key: self._on_preset_toggled(k, on))
            preset_grid.addWidget(btn, i // 4, i % 4)
            self._preset_btns[key] = btn
        preset_grid.setColumnStretch(4, 1)
        outer.addWidget(self._preset_area)

        # Custom (non-preset) keys, shown as removable caps.
        self._chip_area = QWidget()
        self._chip_layout = QGridLayout(self._chip_area)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(6)
        self._chip_layout.setColumnStretch(3, 1)
        outer.addWidget(self._chip_area)

        from withease.gui.ui_utils import em
        self._add_btn = QPushButton(tr("keylist.add_other"))
        self._add_btn.setFixedHeight(max(28, em(1.7)))
        self._add_btn.clicked.connect(self._start_recording)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.addWidget(self._add_btn)
        add_row.addStretch()
        outer.addLayout(add_row)

        self._sync_presets()
        self._rebuild_chips()

    # ------------------------------------------------------------------

    def get_keys(self) -> list[str]:
        return list(self._keys)

    def set_keys(self, keys: list[str]) -> None:
        self._keys = list(keys)
        self._sync_presets()
        self._rebuild_chips()

    # ------------------------------------------------------------------

    def _emit(self) -> None:
        self.keys_changed.emit(list(self._keys))

    def _on_preset_toggled(self, key: str, on: bool) -> None:
        if on and key not in self._keys:
            self._keys.append(key)
            self._emit()
        elif not on and key in self._keys:
            self._keys.remove(key)
            self._emit()

    def _sync_presets(self) -> None:
        """Reflect the current keys in the preset toggle states (no signals)."""
        for key, btn in self._preset_btns.items():
            btn.blockSignals(True)
            btn.setChecked(key in self._keys)
            btn.blockSignals(False)

    def _remove_key(self, key: str) -> None:
        if key in self._keys:
            self._keys.remove(key)
            self._rebuild_chips()
            self._emit()

    def _rebuild_chips(self) -> None:
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        custom = sorted((k for k in self._keys if k not in _PRESET_KEYS),
                        key=lambda k: self._format_key(k).lower())
        for i, key in enumerate(custom):
            chip = _KeyChip(key, self._format_key(key))
            chip.removed.connect(self._remove_key)
            self._chip_layout.addWidget(chip, i // 3, i % 3)
        self._chip_area.setVisible(bool(custom))

    # -- custom key recording ("other key") ----------------------------

    def _start_recording(self) -> None:
        self._recording = True
        self._add_btn.setText(tr("hotkey.press"))
        self.setFocus()
        self.grabKeyboard()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        qt_key = event.key()
        if qt_key in (Qt.Key.Key_Shift, Qt.Key.Key_Control,
                      Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return
        if qt_key == Qt.Key.Key_Escape:
            self._stop_recording()
            return
        pynput_key = self._qt_key_to_pynput(qt_key, event.text())
        self._stop_recording()
        if pynput_key and pynput_key not in self._keys:
            self._keys.append(pynput_key)
            self._sync_presets()      # a preset key recorded this way still lights up
            self._rebuild_chips()
            self._emit()

    def _stop_recording(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self._add_btn.setText(tr("keylist.add_other"))

    # ------------------------------------------------------------------

    @staticmethod
    def _format_key(key: str) -> str:
        if key.startswith("Key."):
            from withease.gui.ui_utils import display_key_name
            return display_key_name(key[4:])
        bare = key.strip("'").lower()
        if bare in ("alt", "ctrl", "shift", "win", "altgr"):
            return tr(f"key.mod.{bare}")
        return key.strip("'").upper()

    @staticmethod
    def _qt_key_to_pynput(qt_key: int, text: str) -> str:
        _map = {
            Qt.Key.Key_F1: "Key.f1",   Qt.Key.Key_F2: "Key.f2",
            Qt.Key.Key_F3: "Key.f3",   Qt.Key.Key_F4: "Key.f4",
            Qt.Key.Key_F5: "Key.f5",   Qt.Key.Key_F6: "Key.f6",
            Qt.Key.Key_F7: "Key.f7",   Qt.Key.Key_F8: "Key.f8",
            Qt.Key.Key_F9: "Key.f9",   Qt.Key.Key_F10: "Key.f10",
            Qt.Key.Key_F11: "Key.f11", Qt.Key.Key_F12: "Key.f12",
            Qt.Key.Key_Space:     "Key.space",
            Qt.Key.Key_Return:    "Key.enter",
            Qt.Key.Key_Enter:     "Key.enter",
            Qt.Key.Key_Tab:       "Key.tab",
            Qt.Key.Key_Backspace: "Key.backspace",
            Qt.Key.Key_Delete:    "Key.delete",
            Qt.Key.Key_Insert:    "Key.insert",
            Qt.Key.Key_Home:      "Key.home",
            Qt.Key.Key_End:       "Key.end",
            Qt.Key.Key_PageUp:    "Key.page_up",
            Qt.Key.Key_PageDown:  "Key.page_down",
            Qt.Key.Key_Up:        "Key.up",
            Qt.Key.Key_Down:      "Key.down",
            Qt.Key.Key_Left:      "Key.left",
            Qt.Key.Key_Right:     "Key.right",
            Qt.Key.Key_CapsLock:  "Key.caps_lock",
            Qt.Key.Key_Escape:    "Key.esc",
        }
        if qt_key in _map:
            return _map[qt_key]
        if text:
            return f"'{text.lower()}'"
        return ""
