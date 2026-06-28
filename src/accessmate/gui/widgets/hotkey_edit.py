"""HotkeyEdit – click to record the next key press as a hotkey.

Usage:
    widget = HotkeyEdit(current_key="Key.f12")
    widget.key_changed.connect(lambda k: print("new key:", k))

The widget stores keys in pynput string format, e.g. "Key.f12" or "'a'".
An empty string means no hotkey assigned.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class HotkeyEdit(QWidget):
    key_changed = Signal(str)  # emits pynput-style key string or ""

    def __init__(self, current_key: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = current_key
        self._recording = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._btn = QPushButton()
        self._btn.setMinimumWidth(140)
        self._btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._btn.clicked.connect(self._start_recording)
        layout.addWidget(self._btn)

        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedWidth(28)
        self._clear_btn.setToolTip("Remove hotkey")
        self._clear_btn.clicked.connect(self._clear)
        layout.addWidget(self._clear_btn)

        self._update_display()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_key(self) -> str:
        return self._key

    def set_key(self, key: str) -> None:
        self._key = key
        self._update_display()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        self._recording = True
        self._btn.setText("[ press a key … ]")
        self.setFocus()
        self.grabKeyboard()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return

        qt_key = event.key()

        # Ignore lone modifier presses
        if qt_key in (
            Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt,
            Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
        ):
            return

        if qt_key == Qt.Key.Key_Escape:
            self._cancel_recording()
            return

        pynput_key = self._qt_key_to_pynput(qt_key, event.text())
        self._recording = False
        self.releaseKeyboard()
        self._key = pynput_key
        self._update_display()
        self.key_changed.emit(self._key)

    def _cancel_recording(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self._update_display()

    def _clear(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self._key = ""
        self._update_display()
        self.key_changed.emit("")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_display(self) -> None:
        if self._key:
            self._btn.setText(self._format_key(self._key))
        else:
            self._btn.setText("— not assigned —")

    @staticmethod
    def _format_key(key: str) -> str:
        """Convert pynput key string to human-readable label."""
        if key.startswith("Key."):
            name = key[4:].upper()
            replacements = {
                "F1": "F1", "F2": "F2", "F3": "F3", "F4": "F4",
                "F5": "F5", "F6": "F6", "F7": "F7", "F8": "F8",
                "F9": "F9", "F10": "F10", "F11": "F11", "F12": "F12",
                "SPACE": "Space", "ENTER": "Enter", "TAB": "Tab",
                "BACKSPACE": "Backspace", "DELETE": "Delete",
                "INSERT": "Insert", "HOME": "Home", "END": "End",
                "PAGE_UP": "Page Up", "PAGE_DOWN": "Page Down",
                "UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
                "CAPS_LOCK": "Caps Lock", "PRINT_SCREEN": "Print Screen",
                "SCROLL_LOCK": "Scroll Lock", "PAUSE": "Pause",
                "NUM_LOCK": "Num Lock",
            }
            return replacements.get(name, name.capitalize())
        # Regular character key like "'a'"
        return key.strip("'").upper()

    @staticmethod
    def _qt_key_to_pynput(qt_key: int, text: str) -> str:
        """Convert a Qt key code to a pynput-compatible string."""
        _map = {
            Qt.Key.Key_F1: "Key.f1", Qt.Key.Key_F2: "Key.f2",
            Qt.Key.Key_F3: "Key.f3", Qt.Key.Key_F4: "Key.f4",
            Qt.Key.Key_F5: "Key.f5", Qt.Key.Key_F6: "Key.f6",
            Qt.Key.Key_F7: "Key.f7", Qt.Key.Key_F8: "Key.f8",
            Qt.Key.Key_F9: "Key.f9", Qt.Key.Key_F10: "Key.f10",
            Qt.Key.Key_F11: "Key.f11", Qt.Key.Key_F12: "Key.f12",
            Qt.Key.Key_Space: "Key.space",
            Qt.Key.Key_Return: "Key.enter", Qt.Key.Key_Enter: "Key.enter",
            Qt.Key.Key_Tab: "Key.tab",
            Qt.Key.Key_Backspace: "Key.backspace",
            Qt.Key.Key_Delete: "Key.delete",
            Qt.Key.Key_Insert: "Key.insert",
            Qt.Key.Key_Home: "Key.home", Qt.Key.Key_End: "Key.end",
            Qt.Key.Key_PageUp: "Key.page_up",
            Qt.Key.Key_PageDown: "Key.page_down",
            Qt.Key.Key_Up: "Key.up", Qt.Key.Key_Down: "Key.down",
            Qt.Key.Key_Left: "Key.left", Qt.Key.Key_Right: "Key.right",
            Qt.Key.Key_CapsLock: "Key.caps_lock",
            Qt.Key.Key_Print: "Key.print_screen",
            Qt.Key.Key_ScrollLock: "Key.scroll_lock",
            Qt.Key.Key_Pause: "Key.pause",
            Qt.Key.Key_NumLock: "Key.num_lock",
            Qt.Key.Key_Escape: "Key.esc",
        }
        if qt_key in _map:
            return _map[qt_key]
        if text:
            return f"'{text.lower()}'"
        return f"Key.{qt_key}"
