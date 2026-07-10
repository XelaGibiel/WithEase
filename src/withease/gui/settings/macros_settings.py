"""Macros module settings page."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.gui import theme

if TYPE_CHECKING:
    from withease.modules.macros import Macro, MacrosModule

_TYPES = ["text", "keys", "app", "mouse"]



_NUM_LABELS: dict[str, str] = {
    "0": "Num 0", "1": "Num 1", "2": "Num 2", "3": "Num 3",
    "4": "Num 4", "5": "Num 5", "6": "Num 6", "7": "Num 7",
    "8": "Num 8", "9": "Num 9",
    "add": "Num +", "subtract": "Num −",
    "multiply": "Num ×", "divide": "Num /",
    "decimal": "Num .", "enter": "Num Enter",
}

def _mod_display(part: str) -> str | None:
    """Localised modifier name (e.g. German 'Strg'), or None if not a modifier."""
    if part in ("alt", "ctrl", "shift", "win", "altgr"):
        return tr(f"key.mod.{part}")
    return None


def _format_single(part: str) -> str:
    if part.startswith("Key.num_"):
        suffix = part[8:]
        return _NUM_LABELS.get(suffix, f"Num {suffix.upper()}")
    if part.startswith("Key."):
        from withease.gui.ui_utils import display_key_name
        return display_key_name(part[4:])
    return part.strip("'").upper()


def _format_key(key: str) -> str:
    """Human-readable label for a pynput key string or combo like ctrl+shift+'a'."""
    if not key:
        return ""
    parts = key.split("+")
    display = []
    for p in parts:
        display.append(_mod_display(p) or _format_single(p))
    return " + ".join(display)


def _macro_content_preview(macro: "Macro") -> str:
    """Short one-line summary of a macro's payload for the table view."""
    if macro.type == "text":
        text = macro.payload.get("text", "")
        return text[:60] + "…" if len(text) > 60 else text
    if macro.type == "keys":
        return _format_key(macro.payload.get("combination", ""))
    if macro.type == "app":
        return macro.payload.get("path", "")
    if macro.type == "mouse":
        steps = macro.payload.get("steps", [])
        return " → ".join(_step_summary(s) for s in steps[:4]) + (
            " …" if len(steps) > 4 else "")
    return ""


# ---------------------------------------------------------------------------
# Key recorder widget (shared by trigger key + macro key + keys payload)
# ---------------------------------------------------------------------------

class _KeyRecorder(QWidget):
    """Button that records a key press (with optional modifiers).

    Stores the result in pynput combo format: e.g. "ctrl+'m'", "Key.f9", "'a'".
    """

    key_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = ""
        self._recording = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        from withease.gui.ui_utils import em
        self._btn = QPushButton(tr("module.macros.dialog.key.record"))
        self._btn.setFixedHeight(max(32, em(2)))
        self._btn.setMinimumWidth(160)
        self._btn.clicked.connect(self._start)
        layout.addWidget(self._btn)

        self._clear_btn = QPushButton("×")
        self._clear_btn.setFixedSize(max(32, em(2)), max(32, em(2)))
        self._clear_btn.setToolTip(tr("module.macros.dialog.key.clear"))
        self._clear_btn.clicked.connect(self._clear)
        self._clear_btn.setVisible(False)
        layout.addWidget(self._clear_btn)

        layout.addStretch()

    def get_key(self) -> str:
        return self._key

    def set_key(self, key: str) -> None:
        self._key = key
        if key:
            display = _format_key(key)
            self._btn.setText(display)
            self._btn.setStyleSheet("font-weight: bold;")
            # Bold text is wider than the size hint computed with the normal
            # font – grow the button so long combos are never cut off.
            from PySide6.QtGui import QFont, QFontMetrics
            bold = QFont(self._btn.font())
            bold.setBold(True)
            needed = QFontMetrics(bold).horizontalAdvance(display) + 32
            self._btn.setMinimumWidth(max(160, needed))
            self._clear_btn.setVisible(True)
        else:
            self._btn.setText(tr("module.macros.dialog.key.record"))
            self._btn.setStyleSheet("")
            self._btn.setMinimumWidth(160)
            self._clear_btn.setVisible(False)
        self.key_changed.emit(key)

    def _clear(self) -> None:
        self.set_key("")

    def _start(self) -> None:
        self._recording = True
        self._btn.setText(tr("module.macros.dialog.key.press"))
        self._btn.setStyleSheet("color: palette(highlight);")
        self._clear_btn.setVisible(False)
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
            # Cancel recording, keep the previously assigned key.
            self._recording = False
            self.releaseKeyboard()
            self.set_key(self._key)
            return
        self._recording = False
        self.releaseKeyboard()

        # Collect modifiers alphabetically (must match sorted(held_mods) in listener)
        mods = event.modifiers()
        mod_parts: list[str] = []
        if mods & Qt.KeyboardModifier.AltModifier:
            mod_parts.append("alt")
        if mods & Qt.KeyboardModifier.ControlModifier:
            mod_parts.append("ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            mod_parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            mod_parts.append("win")

        is_numpad = bool(mods & Qt.KeyboardModifier.KeypadModifier)
        pynput_key = self._qt_to_pynput(qt_key, event.text(), is_numpad,
                                        native_vk=int(event.nativeVirtualKey()))
        if pynput_key:
            self.set_key("+".join(mod_parts + [pynput_key]))
        else:
            # Unrecognised – restore previous
            self.set_key(self._key)

    @staticmethod
    def _qt_to_pynput(qt_key: int, text: str, is_numpad: bool = False,
                      native_vk: int = 0) -> str:
        if is_numpad:
            _numpad = {
                Qt.Key.Key_0: "Key.num_0", Qt.Key.Key_1: "Key.num_1",
                Qt.Key.Key_2: "Key.num_2", Qt.Key.Key_3: "Key.num_3",
                Qt.Key.Key_4: "Key.num_4", Qt.Key.Key_5: "Key.num_5",
                Qt.Key.Key_6: "Key.num_6", Qt.Key.Key_7: "Key.num_7",
                Qt.Key.Key_8: "Key.num_8", Qt.Key.Key_9: "Key.num_9",
                Qt.Key.Key_Plus:     "Key.num_add",
                Qt.Key.Key_Minus:    "Key.num_subtract",
                Qt.Key.Key_Asterisk: "Key.num_multiply",
                Qt.Key.Key_Slash:    "Key.num_divide",
                Qt.Key.Key_Period:   "Key.num_decimal",
                Qt.Key.Key_Enter:    "Key.num_enter",
                Qt.Key.Key_Home:     "Key.num_7",
                Qt.Key.Key_Up:       "Key.num_8",
                Qt.Key.Key_PageUp:   "Key.num_9",
                Qt.Key.Key_Left:     "Key.num_4",
                Qt.Key.Key_Clear:    "Key.num_5",
                Qt.Key.Key_Right:    "Key.num_6",
                Qt.Key.Key_End:      "Key.num_1",
                Qt.Key.Key_Down:     "Key.num_2",
                Qt.Key.Key_PageDown: "Key.num_3",
                Qt.Key.Key_Insert:   "Key.num_0",
                Qt.Key.Key_Delete:   "Key.num_decimal",
            }
            if qt_key in _numpad:
                return _numpad[qt_key]
        _map = {
            Qt.Key.Key_F1:  "Key.f1",  Qt.Key.Key_F2:  "Key.f2",
            Qt.Key.Key_F3:  "Key.f3",  Qt.Key.Key_F4:  "Key.f4",
            Qt.Key.Key_F5:  "Key.f5",  Qt.Key.Key_F6:  "Key.f6",
            Qt.Key.Key_F7:  "Key.f7",  Qt.Key.Key_F8:  "Key.f8",
            Qt.Key.Key_F9:  "Key.f9",  Qt.Key.Key_F10: "Key.f10",
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
            Qt.Key.Key_Escape:    "Key.esc",
            Qt.Key.Key_ScrollLock: "Key.scroll_lock",
            Qt.Key.Key_Pause:     "Key.pause",
            Qt.Key.Key_Print:     "Key.print_screen",
            Qt.Key.Key_NumLock:   "Key.num_lock",
        }
        if qt_key in _map:
            return _map[qt_key]
        if 0x20 <= qt_key <= 0x7E:
            return f"'{chr(qt_key).lower()}'"
        # Ctrl+Alt acts as AltGr on Windows and composes a character
        # (Ctrl+Alt+M → 'µ'); the native VK still names the physical key.
        if native_vk:
            from withease.core.win_keyboard_hook import vk_to_combo_str
            mapped = vk_to_combo_str(native_vk)
            if mapped:
                return mapped
        return ""


def _step_summary(step: dict[str, Any]) -> str:
    """One-line human-readable description of a sequence step."""
    kind = step.get("type", "")
    if kind == "mouse":
        action = tr(f"module.macros.step.mouse.{step.get('action', 'left')}")
        pos = step.get("pos")
        if pos:
            return f"{action} @ ({pos[0]}, {pos[1]})"
        return f"{action} ({tr('module.macros.step.mouse.current')})"
    if kind == "text":
        text = step.get("text", "")
        short = text[:30] + "…" if len(text) > 30 else text
        return f"{tr('module.macros.step.text')}: {short}"
    if kind == "keys":
        return f"{tr('module.macros.step.keys')}: {_format_key(step.get('combination', ''))}"
    if kind == "wait":
        return f"{tr('module.macros.step.wait')}: {step.get('ms', 0)} ms"
    if kind == "window":
        return f"{tr('module.macros.step.window')}: {step.get('title', '')}"
    return kind


_STEP_TYPES = ["mouse", "text", "keys", "wait", "window"]


class _StepDialog(QDialog):
    """Editor for a single sequence step (mouse / text / keys / wait / window)."""

    # Position capture confirmation arrives from the low-level hook thread.
    _pos_captured = Signal(int, int)
    _capture_cancelled = Signal()

    def __init__(self, step: dict[str, Any] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("module.macros.step.title"))
        self.setMinimumWidth(380)
        self._capturing = False
        self._pos_captured.connect(self._on_pos_captured)
        self._capture_cancelled.connect(self._stop_capture)
        self._build_ui()
        from withease.gui.ui_utils import compact_fields, em
        compact_fields(self)
        # The window-title combo must NOT size itself to the longest open
        # window title (those can be extremely long and would blow up the
        # dialog width) – cap it and let the popup show the full titles.
        self._window_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._window_combo.setMinimumContentsLength(24)
        self._window_combo.setMaximumWidth(max(340, em(20)))
        if step:
            self._load(step)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self._type_box = QComboBox()
        for t in _STEP_TYPES:
            self._type_box.addItem(tr(f"module.macros.step.type.{t}"), t)
        self._type_box.currentIndexChanged.connect(
            lambda i: self._stack.setCurrentIndex(i))
        form.addRow(tr("module.macros.step.type"), self._type_box)
        layout.addLayout(form)

        self._stack = QStackedWidget()

        # Page 0: mouse click
        mouse_page = QWidget()
        ml = QVBoxLayout(mouse_page)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        self._action_box = QComboBox()
        for a in ("left", "right", "double"):
            self._action_box.addItem(tr(f"module.macros.step.mouse.{a}"), a)
        ml.addWidget(self._action_box)
        self._fixed_cb = QCheckBox(tr("module.macros.step.mouse.fixed"))
        self._fixed_cb.toggled.connect(self._on_fixed_toggled)
        ml.addWidget(self._fixed_cb)

        pos_col = QVBoxLayout()
        pos_col.setSpacing(4)
        coord_row = QHBoxLayout()
        self._pos_x = QSpinBox()
        self._pos_x.setRange(0, 20000)
        self._pos_y = QSpinBox()
        self._pos_y.setRange(0, 20000)
        coord_row.addWidget(QLabel("X:"))
        coord_row.addWidget(self._pos_x)
        coord_row.addWidget(QLabel("Y:"))
        coord_row.addWidget(self._pos_y)
        coord_row.addStretch()
        pos_col.addLayout(coord_row)
        self._capture_btn = QPushButton(tr("module.macros.step.mouse.capture"))
        self._capture_btn.clicked.connect(self._start_capture)
        pos_col.addWidget(self._capture_btn)
        self._capture_hint = QLabel(tr("module.macros.step.mouse.capture.hint"))
        self._capture_hint.setStyleSheet(theme.hint_style())
        self._capture_hint.setWordWrap(True)
        pos_col.addWidget(self._capture_hint)
        self._pos_row_widget = QWidget()
        self._pos_row_widget.setLayout(pos_col)
        ml.addWidget(self._pos_row_widget)
        ml.addStretch()
        self._on_fixed_toggled(False)
        self._stack.addWidget(mouse_page)

        # Page 1: text
        text_page = QWidget()
        tl = QVBoxLayout(text_page)
        tl.setContentsMargins(0, 0, 0, 0)
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText(
            tr("module.macros.dialog.text.placeholder"))
        tl.addWidget(self._text_edit)
        tl.addStretch()
        self._stack.addWidget(text_page)

        # Page 2: key combination
        keys_page = QWidget()
        kl = QVBoxLayout(keys_page)
        kl.setContentsMargins(0, 0, 0, 0)
        self._keys_rec = _KeyRecorder()
        kl.addWidget(self._keys_rec)
        kl.addStretch()
        self._stack.addWidget(keys_page)

        # Page 3: wait
        wait_page = QWidget()
        wl = QVBoxLayout(wait_page)
        wl.setContentsMargins(0, 0, 0, 0)
        self._wait_ms = QSpinBox()
        self._wait_ms.setRange(10, 60000)
        self._wait_ms.setSuffix(" ms")
        self._wait_ms.setValue(500)
        wl.addWidget(self._wait_ms)
        wl.addStretch()
        self._stack.addWidget(wait_page)

        # Page 4: bring window to foreground
        win_page = QWidget()
        wnl = QVBoxLayout(win_page)
        wnl.setContentsMargins(0, 0, 0, 0)
        wnl.setSpacing(4)
        wnl.addWidget(QLabel(tr("module.macros.step.window.title_label")))
        win_row = QHBoxLayout()
        win_row.setSpacing(4)
        self._window_combo = QComboBox()
        self._window_combo.setEditable(True)
        self._window_combo.setMinimumWidth(220)
        win_row.addWidget(self._window_combo, 1)
        from withease.gui.ui_utils import em
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedWidth(max(32, em(2)))
        refresh_btn.setToolTip(tr("module.macros.step.window.refresh"))
        refresh_btn.clicked.connect(self._refresh_windows)
        win_row.addWidget(refresh_btn)
        wnl.addLayout(win_row)
        win_hint = QLabel(tr("module.macros.step.window.hint"))
        win_hint.setStyleSheet(theme.hint_style())
        win_hint.setWordWrap(True)
        wnl.addWidget(win_hint)
        wnl.addStretch()
        self._refresh_windows()
        self._stack.addWidget(win_page)

        layout.addWidget(self._stack)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_fixed_toggled(self, checked: bool) -> None:
        self._pos_row_widget.setVisible(checked)

    def _start_capture(self) -> None:
        """Arm global capture: user moves the cursor anywhere on screen and
        confirms with Space (Escape cancels).  Uses the shared low-level hook
        so it works even while another window has focus."""
        if self._capturing:
            return
        self._capturing = True
        self._capture_btn.setText(tr("module.macros.step.mouse.capture.active"))
        from withease.core.win_keyboard_hook import shared_keyboard_hook
        shared_keyboard_hook.subscribe(self._capture_key_event)

    def _capture_key_event(self, vk: int, scan: int, extended: bool,
                           injected: bool, is_press: bool) -> bool:
        """Hook callback (hook thread!) – Space confirms, Escape cancels."""
        if injected or not is_press:
            return False
        if vk == 0x20:  # Space
            pos = QCursor.pos()
            self._pos_captured.emit(pos.x(), pos.y())
            return True   # swallow the space
        if vk == 0x1B:  # Escape
            self._capture_cancelled.emit()
            return True
        return False

    def _on_pos_captured(self, x: int, y: int) -> None:
        self._pos_x.setValue(max(0, x))
        self._pos_y.setValue(max(0, y))
        self._stop_capture()

    def _stop_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        from withease.core.win_keyboard_hook import shared_keyboard_hook
        shared_keyboard_hook.unsubscribe(self._capture_key_event)
        self._capture_btn.setText(tr("module.macros.step.mouse.capture"))

    def done(self, result: int) -> None:  # type: ignore[override]
        self._stop_capture()  # never leave the hook subscription dangling
        super().done(result)

    # -- Window picker ---------------------------------------------------

    def _refresh_windows(self) -> None:
        """Fill the combo with the titles of all visible top-level windows."""
        current = self._window_combo.currentText()
        self._window_combo.clear()
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            titles: list[str] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def enum_proc(hwnd: int, _lparam: int) -> bool:
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value.strip()
                        if title and title not in titles:
                            titles.append(title)
                return True

            user32.EnumWindows(enum_proc, 0)
            self._window_combo.addItems(sorted(titles, key=str.lower))
        except Exception:
            pass
        self._window_combo.setEditText(current)

    def _load(self, step: dict[str, Any]) -> None:
        kind = step.get("type", "mouse")
        idx = _STEP_TYPES.index(kind) if kind in _STEP_TYPES else 0
        self._type_box.setCurrentIndex(idx)
        self._stack.setCurrentIndex(idx)
        if kind == "mouse":
            action = step.get("action", "left")
            for i in range(self._action_box.count()):
                if self._action_box.itemData(i) == action:
                    self._action_box.setCurrentIndex(i)
            pos = step.get("pos")
            if pos:
                self._fixed_cb.setChecked(True)
                self._pos_x.setValue(int(pos[0]))
                self._pos_y.setValue(int(pos[1]))
        elif kind == "text":
            self._text_edit.setText(step.get("text", ""))
        elif kind == "keys":
            self._keys_rec.blockSignals(True)
            self._keys_rec.set_key(step.get("combination", ""))
            self._keys_rec.blockSignals(False)
        elif kind == "wait":
            self._wait_ms.setValue(int(step.get("ms", 500)))
        elif kind == "window":
            self._window_combo.setEditText(step.get("title", ""))

    def result_step(self) -> dict[str, Any]:
        kind = self._type_box.currentData()
        if kind == "mouse":
            step: dict[str, Any] = {
                "type": "mouse",
                "action": self._action_box.currentData(),
            }
            if self._fixed_cb.isChecked():
                step["pos"] = [self._pos_x.value(), self._pos_y.value()]
            return step
        if kind == "text":
            return {"type": "text", "text": self._text_edit.text()}
        if kind == "keys":
            return {"type": "keys", "combination": self._keys_rec.get_key()}
        if kind == "window":
            return {"type": "window",
                    "title": self._window_combo.currentText().strip()}
        return {"type": "wait", "ms": self._wait_ms.value()}


# ---------------------------------------------------------------------------
# Add / Edit dialog
# ---------------------------------------------------------------------------

class _MacroDialog(QDialog):
    def __init__(self, macro: "Macro | None" = None,
                 existing_keys: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._macro = macro
        # Keys already used by other macros (for duplicate detection)
        self._existing_keys = existing_keys or []
        title_key = "module.macros.dialog.edit" if macro else "module.macros.dialog.add"
        self.setWindowTitle(tr(title_key))
        self.setMinimumWidth(440)
        self._build_ui()
        from withease.gui.ui_utils import compact_fields
        compact_fields(self)
        if macro:
            self._load(macro)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self._name = QLineEdit()
        self._name.setPlaceholderText(tr("module.macros.dialog.name"))
        form.addRow(tr("module.macros.dialog.name"), self._name)

        self._key_rec = _KeyRecorder()
        self._key_rec.key_changed.connect(self._check_duplicate)
        form.addRow(tr("module.macros.dialog.key"), self._key_rec)

        self._dup_warning = QLabel()
        self._dup_warning.setStyleSheet(theme.warn_style())
        self._dup_warning.hide()
        form.addRow("", self._dup_warning)

        self._type_box = QComboBox()
        for t in _TYPES:
            self._type_box.addItem(tr(f"module.macros.type.{t}"), t)
        self._type_box.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("module.macros.dialog.type"), self._type_box)

        layout.addLayout(form)

        # Payload area – stacked per type
        self._stack = QStackedWidget()

        # Page 0: text – plain text input
        text_page = QWidget()
        tl = QVBoxLayout(text_page)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(QLabel(tr("module.macros.dialog.text")))
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText(tr("module.macros.dialog.text.placeholder"))
        tl.addWidget(self._text_edit)
        tl.addStretch()
        self._stack.addWidget(text_page)

        # Page 1: keys – use _KeyRecorder for reliable input
        keys_page = QWidget()
        kl = QVBoxLayout(keys_page)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(4)
        kl.addWidget(QLabel(tr("module.macros.dialog.combination")))
        self._keys_rec = _KeyRecorder()
        kl.addWidget(self._keys_rec)
        kl.addStretch()
        self._stack.addWidget(keys_page)

        # Page 2: app – executable path (with browse) + optional arguments
        app_page = QWidget()
        al = QVBoxLayout(app_page)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(4)
        al.addWidget(QLabel(tr("module.macros.dialog.app.path")))
        path_row = QHBoxLayout()
        path_row.setSpacing(4)
        self._app_path = QLineEdit()
        self._app_path.setPlaceholderText(
            tr("module.macros.dialog.app.path.placeholder"))
        path_row.addWidget(self._app_path)
        browse_btn = QPushButton(tr("module.macros.dialog.app.browse"))
        browse_btn.clicked.connect(self._browse_app)
        path_row.addWidget(browse_btn)
        al.addLayout(path_row)
        al.addWidget(QLabel(tr("module.macros.dialog.app.args")))
        self._app_args = QLineEdit()
        self._app_args.setPlaceholderText(
            tr("module.macros.dialog.app.args.placeholder"))
        al.addWidget(self._app_args)
        al.addStretch()
        self._stack.addWidget(app_page)

        # Page 3: mouse/keyboard sequence – ordered list of steps
        seq_page = QWidget()
        sl = QVBoxLayout(seq_page)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        sl.addWidget(QLabel(tr("module.macros.dialog.sequence")))
        self._steps: list[dict[str, Any]] = []
        self._step_list = QListWidget()
        self._step_list.setMinimumHeight(120)
        self._step_list.doubleClicked.connect(lambda _: self._edit_step())
        from PySide6.QtWidgets import QStyleFactory
        self._step_list_style = QStyleFactory.create("Fusion")
        if self._step_list_style is not None:
            self._step_list.setStyle(self._step_list_style)
        theme.style_item_view(self._step_list, "QListWidget")
        sl.addWidget(self._step_list)
        step_btns = QHBoxLayout()
        for label_key, slot in [
            ("module.macros.dialog.sequence.add", self._add_step),
            ("module.macros.dialog.sequence.edit", self._edit_step),
            ("module.macros.dialog.sequence.remove", self._remove_step),
        ]:
            b = QPushButton(tr(label_key))
            b.clicked.connect(slot)
            step_btns.addWidget(b)
        from withease.gui.ui_utils import em
        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(max(32, em(2)))
        up_btn.clicked.connect(lambda: self._move_step(-1))
        step_btns.addWidget(up_btn)
        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(max(32, em(2)))
        down_btn.clicked.connect(lambda: self._move_step(1))
        step_btns.addWidget(down_btn)
        step_btns.addStretch()
        sl.addLayout(step_btns)
        self._stack.addWidget(seq_page)

        layout.addWidget(self._stack)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _on_type_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)

    # -- Sequence step management ---------------------------------------

    def _refresh_step_list(self) -> None:
        self._step_list.clear()
        for step in self._steps:
            self._step_list.addItem(_step_summary(step))

    def _add_step(self) -> None:
        dlg = _StepDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._steps.append(dlg.result_step())
            self._refresh_step_list()
            self._step_list.setCurrentRow(len(self._steps) - 1)

    def _edit_step(self) -> None:
        row = self._step_list.currentRow()
        if row < 0:
            return
        dlg = _StepDialog(step=self._steps[row], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._steps[row] = dlg.result_step()
            self._refresh_step_list()
            self._step_list.setCurrentRow(row)

    def _remove_step(self) -> None:
        row = self._step_list.currentRow()
        if row >= 0:
            self._steps.pop(row)
            self._refresh_step_list()
            self._step_list.setCurrentRow(min(row, len(self._steps) - 1))

    def _move_step(self, delta: int) -> None:
        row = self._step_list.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < len(self._steps)):
            return
        self._steps[row], self._steps[new] = self._steps[new], self._steps[row]
        self._refresh_step_list()
        self._step_list.setCurrentRow(new)

    def _browse_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("module.macros.dialog.app.browse.title"),
            "",
            tr("module.macros.dialog.app.browse.filter"),
        )
        if path:
            self._app_path.setText(path)

    def _check_duplicate(self, key: str) -> None:
        if key and key in self._existing_keys:
            self._dup_warning.setText(tr("module.macros.dialog.key.duplicate"))
            self._dup_warning.show()
        else:
            self._dup_warning.hide()

    def _load(self, macro: "Macro") -> None:
        self._name.setText(macro.label)
        self._key_rec.blockSignals(True)
        self._key_rec.set_key(macro.trigger_key)
        self._key_rec.blockSignals(False)
        idx = _TYPES.index(macro.type) if macro.type in _TYPES else 0
        self._type_box.setCurrentIndex(idx)
        self._stack.setCurrentIndex(idx)
        if macro.type == "text":
            self._text_edit.setText(macro.payload.get("text", ""))
        elif macro.type == "keys":
            self._keys_rec.blockSignals(True)
            self._keys_rec.set_key(macro.payload.get("combination", ""))
            self._keys_rec.blockSignals(False)
        elif macro.type == "app":
            self._app_path.setText(macro.payload.get("path", ""))
            self._app_args.setText(" ".join(macro.payload.get("args", [])))
        elif macro.type == "mouse":
            self._steps = [dict(s) for s in macro.payload.get("steps", [])]
            self._refresh_step_list()

    def result_data(self) -> dict[str, Any]:
        t = self._type_box.currentData()
        if t == "text":
            payload: dict[str, Any] = {"text": self._text_edit.text()}
        elif t == "keys":
            payload = {"combination": self._keys_rec.get_key()}
        elif t == "app":
            payload = {
                "path": self._app_path.text().strip(),
                "args": self._app_args.text().split(),
            }
        else:  # mouse sequence
            payload = {"steps": self._steps}
        name = self._name.text().strip()
        return {
            "id": self._macro.id if self._macro else str(uuid.uuid4()),
            "label": name if name else tr("module.macros.unnamed"),
            "trigger_key": self._key_rec.get_key(),
            "type": t,
            "payload": payload,
        }


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

class MacrosSettingsWidget(QWidget):
    def __init__(self, module: "MacrosModule", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._build_ui()
        from withease.gui.settings.module_sync import sync_module_checkbox
        sync_module_checkbox(self, module, self._enabled_cb,
                             self._update_enabled_state)

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Module toggle ──────────────────────────────────────────
        self._enabled_cb = QCheckBox(tr("module.macros.enabled"))
        self._enabled_cb.setChecked(self._module.enabled)
        self._enabled_cb.setStyleSheet(theme.title_style())
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── Trigger key ────────────────────────────────────────────
        trigger_form = QFormLayout()
        trigger_form.setSpacing(8)

        self._trigger_edit = _KeyRecorder()
        self._trigger_edit.blockSignals(True)
        self._trigger_edit.set_key(self._module._settings.get("trigger_key", ""))
        self._trigger_edit.blockSignals(False)
        self._trigger_edit.key_changed.connect(self._on_trigger_changed)
        trigger_form.addRow(tr("module.macros.trigger_key"), self._trigger_edit)

        # ── Indicator chip size + preview ──────────────────────────
        size_row = QHBoxLayout()
        self._chip_size = QSpinBox()
        self._chip_size.setRange(16, 64)
        self._chip_size.setSuffix(" px")
        self._chip_size.setValue(self._module._settings.get("chip_size", 28))
        self._chip_size.valueChanged.connect(self._on_chip_size_changed)
        size_row.addWidget(self._chip_size)

        self._preview_cb = QCheckBox(tr("module.macros.chip_size.preview"))
        self._preview_cb.toggled.connect(self._on_preview_toggled)
        size_row.addWidget(self._preview_cb)
        size_row.addStretch()
        trigger_form.addRow(tr("module.macros.chip_size"), size_row)

        layout.addLayout(trigger_form)

        # ── Macro list ─────────────────────────────────────────────
        list_label = QLabel(tr("module.macros.list"))
        list_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(list_label)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            tr("module.macros.col.name"),
            tr("module.macros.col.key"),
            tr("module.macros.col.type"),
            tr("module.macros.col.content"),
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(120)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(lambda _: self._on_edit())
        self._table.setShowGrid(False)
        # The native Windows 11 style paints an accent-coloured bar on the
        # left edge of every selected cell, which looks like stray vertical
        # strokes.  Fusion has no such decoration, so use it for this widget.
        from PySide6.QtWidgets import QStyleFactory
        self._table_style = QStyleFactory.create("Fusion")
        if self._table_style is not None:
            self._table.setStyle(self._table_style)
        theme.style_item_view(self._table, "QTableWidget")
        layout.addWidget(self._table)

        # ── Buttons ────────────────────────────────────────────────
        from withease.gui.ui_utils import em
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton(tr("module.macros.add"))
        self._add_btn.setFixedHeight(max(28, em(1.7)))
        self._add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self._add_btn)

        self._edit_btn = QPushButton(tr("module.macros.edit"))
        self._edit_btn.setFixedHeight(max(28, em(1.7)))
        self._edit_btn.clicked.connect(self._on_edit)
        btn_row.addWidget(self._edit_btn)

        self._del_btn = QPushButton(tr("module.macros.delete"))
        self._del_btn.setFixedHeight(max(28, em(1.7)))
        self._del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._del_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._refresh_table()
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _on_chip_size_changed(self, value: int) -> None:
        self._module._settings["chip_size"] = value
        self._module.on_settings_changed()
        bus.publish("macros.chip_size", size=value)

    def _on_preview_toggled(self, active: bool) -> None:
        bus.publish("macros.preview", active=active)

    def hideEvent(self, event: object) -> None:
        if self._preview_cb.isChecked():
            self._preview_cb.setChecked(False)
        super().hideEvent(event)  # type: ignore[arg-type]

    def _refresh_table(self) -> None:
        macros = self._module._macros
        self._table.clearSpans()
        if not macros:
            self._table.setRowCount(1)
            placeholder = QTableWidgetItem(tr("module.macros.no_macros"))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setForeground(QBrush(QColor("gray")))
            self._table.setItem(0, 0, placeholder)
            self._table.setSpan(0, 0, 1, 4)
            self._edit_btn.setEnabled(False)
            self._del_btn.setEnabled(False)
        else:
            self._table.setRowCount(len(macros))
            for row, m in enumerate(macros):
                self._table.setItem(row, 0, QTableWidgetItem(m.label))
                self._table.setItem(row, 1, QTableWidgetItem(_format_key(m.trigger_key)))
                self._table.setItem(row, 2, QTableWidgetItem(tr(f"module.macros.type.{m.type}")))
                self._table.setItem(row, 3, QTableWidgetItem(_macro_content_preview(m)))
            self._edit_btn.setEnabled(True)
            self._del_btn.setEnabled(True)

    def _selected_index(self) -> int:
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _existing_keys_except(self, exclude_idx: int) -> list[str]:
        return [
            m.trigger_key
            for i, m in enumerate(self._module._macros)
            if i != exclude_idx and m.trigger_key
        ]

    def _on_add(self) -> None:
        dlg = _MacroDialog(
            existing_keys=self._existing_keys_except(-1),
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            from withease.modules.macros import Macro
            self._module._macros.append(Macro(**dlg.result_data()))
            self._module.on_settings_changed()
            self._refresh_table()

    def _on_edit(self) -> None:
        idx = self._selected_index()
        if idx < 0:
            return
        macro = self._module._macros[idx]
        dlg = _MacroDialog(
            macro=macro,
            existing_keys=self._existing_keys_except(idx),
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            from withease.modules.macros import Macro
            self._module._macros[idx] = Macro(**dlg.result_data())
            self._module.on_settings_changed()
            self._refresh_table()

    def _on_delete(self) -> None:
        idx = self._selected_index()
        if idx < 0 and self._module._macros:
            idx = len(self._module._macros) - 1
        if idx >= 0:
            self._module._macros.pop(idx)
            self._module.on_settings_changed()
            self._refresh_table()

    def _on_trigger_changed(self, key: str) -> None:
        self._module._settings["trigger_key"] = key
        self._module.on_settings_changed()

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _update_enabled_state(self, enabled: bool) -> None:
        for w in (self._trigger_edit, self._chip_size, self._preview_cb,
                  self._table, self._add_btn, self._edit_btn, self._del_btn):
            w.setEnabled(enabled)
