"""KeyListEdit – records individual key presses into a removable list.

Usage:
    widget = KeyListEdit(current_keys=["Key.space", "Key.enter"])
    widget.keys_changed.connect(lambda keys: print(keys))
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent

from withease.core.i18n import tr
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# PySide6 doesn't ship QFlowLayout – use a simple wrapping implementation

class _KeyChip(QWidget):
    """A single removable key chip."""
    removed = Signal(str)

    def __init__(self, key: str, label: str) -> None:
        super().__init__()
        self._key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 2, 2)
        layout.setSpacing(2)

        lbl = QLabel(label)
        layout.addWidget(lbl)

        from withease.gui.ui_utils import em
        btn = QPushButton("✕")
        btn.setFixedSize(em(1.1), em(1.1))
        btn.setFlat(True)
        btn.setStyleSheet("color: palette(mid);")
        btn.clicked.connect(lambda: self.removed.emit(self._key))
        layout.addWidget(btn)

        self.setStyleSheet(
            "QWidget { background: palette(button); border: 1px solid palette(mid);"
            " border-radius: 10px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class KeyListEdit(QWidget):
    """Records key presses one at a time into a visual list."""

    keys_changed = Signal(list)  # emits list[str] of pynput key strings

    def __init__(self, current_keys: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keys: list[str] = list(current_keys or [])
        self._recording = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # Chip area – two-column grid, sorted alphabetically
        self._chip_area = QWidget()
        self._chip_layout = QGridLayout(self._chip_area)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(4)
        self._chip_layout.setColumnStretch(0, 1)
        self._chip_layout.setColumnStretch(1, 1)
        outer.addWidget(self._chip_area)

        # Add button
        from withease.gui.ui_utils import em
        self._add_btn = QPushButton(tr("keylist.add"))
        self._add_btn.setFixedHeight(max(28, em(1.7)))
        self._add_btn.clicked.connect(self._start_recording)
        outer.addWidget(self._add_btn)

        self._rebuild_chips()

    # ------------------------------------------------------------------

    def get_keys(self) -> list[str]:
        return list(self._keys)

    def set_keys(self, keys: list[str]) -> None:
        self._keys = list(keys)
        self._rebuild_chips()

    # ------------------------------------------------------------------

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
            self._rebuild_chips()
            self.keys_changed.emit(self._keys)

    def _stop_recording(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self._add_btn.setText(tr("keylist.add"))

    def _remove_key(self, key: str) -> None:
        if key in self._keys:
            self._keys.remove(key)
            self._rebuild_chips()
            self.keys_changed.emit(self._keys)

    def _rebuild_chips(self) -> None:
        # Clear existing grid
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Sort alphabetically by display label
        sorted_keys = sorted(self._keys, key=lambda k: self._format_key(k).lower())

        for i, key in enumerate(sorted_keys):
            chip = _KeyChip(key, self._format_key(key))
            chip.removed.connect(self._remove_key)
            row, col = divmod(i, 2)
            self._chip_layout.addWidget(chip, row, col)

        self._chip_area.setVisible(bool(self._keys))
        self._chip_area.updateGeometry()
        self.updateGeometry()

    # ------------------------------------------------------------------

    @staticmethod
    def _format_key(key: str) -> str:
        if key.startswith("Key."):
            from withease.gui.ui_utils import display_key_name
            return display_key_name(key[4:])
        bare = key.strip("'").lower()
        if bare in ("alt", "ctrl", "shift", "win", "altgr"):
            return tr(f"key.mod.{bare}")   # "Strg" instead of "CTRL"
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
