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
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.gui import theme


class HotkeyEdit(QWidget):
    key_changed = Signal(str)  # emits pynput-style key string or ""

    # All live HotkeyEdit widgets, so a change in one re-checks conflicts in all.
    _live: list["HotkeyEdit"] = []
    _bus_hooked = False

    def __init__(self, current_key: str = "", action_id: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = current_key
        self._action_id = action_id  # the ActionManager action this hotkey feeds
        self._recording = False

        HotkeyEdit._live.append(self)
        self.destroyed.connect(lambda: self._forget(self))

        # One class-level bus subscription: whenever any module's settings
        # change (tool enabled/disabled, hotkey assigned), re-check conflicts
        # in every live field so warnings appear/disappear everywhere.
        if not HotkeyEdit._bus_hooked:
            HotkeyEdit._bus_hooked = True
            bus.subscribe("module.settings_changed",
                          lambda **_: HotkeyEdit._recheck_all())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._btn = QPushButton()
        self._btn.setMinimumWidth(140)
        self._btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._btn.clicked.connect(self._start_recording)
        layout.addWidget(self._btn)

        from withease.gui.ui_utils import em
        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedWidth(max(28, em(1.7)))
        self._clear_btn.setToolTip(tr("hotkey.clear"))
        self._clear_btn.clicked.connect(self._clear)
        layout.addWidget(self._clear_btn)
        # Keep the button and ✕ together at the left instead of letting the
        # button stretch across the whole form row.
        layout.addStretch()

        outer.addWidget(row)

        self._warning = QLabel()
        self._warning.setStyleSheet(theme.warn_style())
        self._warning.setWordWrap(True)
        self._warning.hide()
        outer.addWidget(self._warning)

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
        self._btn.setText(tr("hotkey.press"))
        self.setFocus()
        self.grabKeyboard()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return

        qt_key = event.key()

        if qt_key in (
            Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt,
            Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
        ):
            return

        if qt_key == Qt.Key.Key_Escape:
            self._cancel_recording()
            return

        is_numpad = bool(event.modifiers() & Qt.KeyboardModifier.KeypadModifier)
        pynput_key = self._qt_key_to_pynput(
            qt_key, event.text(), is_numpad,
            native_vk=int(event.nativeVirtualKey()))
        self._recording = False
        self.releaseKeyboard()

        # Include held modifiers, alphabetically – must match the combo
        # format produced by current_combo_str() at fire time.  We OR Qt's
        # view with the actual OS state, because Sticky-Keys hold modifiers
        # at the OS level without Qt reporting them as event modifiers.
        mods = event.modifiers()
        held = set()
        try:
            from withease.core.keyboard_hook import effective_modifiers
            held = set(effective_modifiers())
        except Exception:
            pass
        mod_parts: list[str] = []
        if (mods & Qt.KeyboardModifier.AltModifier) or "alt" in held:
            mod_parts.append("alt")
        if (mods & Qt.KeyboardModifier.ControlModifier) or "ctrl" in held:
            mod_parts.append("ctrl")
        if (mods & Qt.KeyboardModifier.ShiftModifier) or "shift" in held:
            mod_parts.append("shift")
        if (mods & Qt.KeyboardModifier.MetaModifier) or "win" in held:
            mod_parts.append("win")
        if pynput_key and mod_parts:
            pynput_key = "+".join(mod_parts + [pynput_key])

        # Reject keys already assigned in ANY other hotkey field (across all
        # modules, enabled or not) – duplicates never enter a field.
        taken_by = self._used_elsewhere(pynput_key)
        if taken_by:
            self._update_display()  # restore previous key on the button
            self._warning.setText(tr("hotkey.taken", name=taken_by))
            self._warning.show()
            return

        self._key = pynput_key
        self._update_display()
        # Emit first so the owning module assigns the trigger in the
        # ActionManager, then re-check conflicts across all fields.
        self.key_changed.emit(self._key)
        self._recheck_all()

    def _cancel_recording(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self._update_display()

    def _clear(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self._key = ""
        self._warning.hide()
        self._update_display()
        self.key_changed.emit("")
        self._recheck_all()

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _used_elsewhere(self, key: str) -> str | None:
        """Return the label of whatever already uses `key`, or None if free.

        Checks every other live hotkey field (all modules, regardless of
        enabled state) so a duplicate can never be entered in the first place.
        """
        if not key:
            return None
        for w in list(HotkeyEdit._live):
            try:
                if w is self or w._key != key:
                    continue
            except RuntimeError:
                HotkeyEdit._forget(w)
                continue
            # Prefer the action label for a readable message.
            try:
                from withease.core.action_manager import action_manager
                for a in action_manager.get_all():
                    if a.id == w._action_id:
                        return a.label
            except Exception:
                pass
            return self._format_key(key)
        return None

    @staticmethod
    def _forget(widget: "HotkeyEdit") -> None:
        try:
            HotkeyEdit._live.remove(widget)
        except ValueError:
            pass

    @staticmethod
    def _recheck_all() -> None:
        for w in list(HotkeyEdit._live):
            try:
                w._check_conflict()
            except RuntimeError:
                HotkeyEdit._forget(w)  # underlying C++ widget already deleted

    def showEvent(self, event: object) -> None:  # type: ignore[override]
        self._check_conflict()
        super().showEvent(event)  # type: ignore[arg-type]

    def _check_conflict(self) -> None:
        """Warn if another *active* tool uses the same hotkey.

        Conflicts are computed from the ActionManager's assigned triggers,
        which are only set for enabled tools – so a disabled tool neither
        raises nor receives a warning.
        """
        if not self._key or not self._action_id:
            self._hide_warning()
            return
        try:
            from withease.core.action_manager import action_manager
            actions = {a.id: a for a in action_manager.get_all()}
            mine = actions.get(self._action_id)
            # Only warn while this tool's hotkey is actually active.
            if mine is None or not mine.trigger:
                self._hide_warning()
                return
            conflicts = [
                a.label for a in actions.values()
                if a.id != self._action_id and a.trigger == mine.trigger
            ]
            if conflicts:
                self._warning.setText(
                    tr("hotkey.conflict", names=", ".join(conflicts)))
                self._warning.show()
            else:
                self._hide_warning()
        except Exception:
            self._hide_warning()

    def _hide_warning(self) -> None:
        self._warning.setText("")
        self._warning.hide()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_display(self) -> None:
        if self._key:
            self._btn.setText(self._format_key(self._key))
        else:
            self._btn.setText(tr("hotkey.not_assigned"))

    @staticmethod
    def _format_key(key: str) -> str:
        if "+" in key:
            # Localised modifier names (e.g. German "Strg" instead of "Ctrl").
            _mods = {m: tr(f"key.mod.{m}")
                     for m in ("alt", "ctrl", "shift", "win", "altgr")}
            parts = key.split("+")
            display = [_mods.get(p, HotkeyEdit._format_key(p)) for p in parts]
            return " + ".join(display)
        if key.startswith("Key.num_"):
            suffix = key[8:]
            _labels = {
                "0": "Num 0", "1": "Num 1", "2": "Num 2", "3": "Num 3",
                "4": "Num 4", "5": "Num 5", "6": "Num 6", "7": "Num 7",
                "8": "Num 8", "9": "Num 9",
                "add": "Num +", "subtract": "Num −",
                "multiply": "Num ×", "divide": "Num /",
                "decimal": "Num .", "enter": "Num Enter",
            }
            return _labels.get(suffix, f"Num {suffix.upper()}")
        if key.startswith("Key."):
            from withease.gui.ui_utils import display_key_name
            return display_key_name(key[4:])
        bare = key.strip("'").lower()
        if bare in ("alt", "ctrl", "shift", "win", "altgr"):
            return tr(f"key.mod.{bare}")   # "Strg" instead of "CTRL"
        return key.strip("'").upper()

    @staticmethod
    def _qt_key_to_pynput(qt_key: int, text: str, is_numpad: bool = False,
                          native_vk: int = 0) -> str:
        # Numpad digits and operators – must be checked before the regular key map
        # because numpad digits share Qt.Key codes with regular digits.
        if is_numpad:
            _numpad_map = {
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
                # NumLock-off keys (cursor cluster on numpad)
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
            if qt_key in _numpad_map:
                return _numpad_map[qt_key]

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
        # Use chr() for printable ASCII keys to get the layout-independent character.
        if 0x20 <= qt_key <= 0x7E:
            return f"'{chr(qt_key).lower()}'"
        # Non-ASCII qt_key: happens e.g. for Ctrl+Alt+<letter>, which Windows
        # treats as AltGr and composes a character (Ctrl+Alt+M → 'µ').  The
        # native virtual-key code still identifies the physical key ('M'),
        # which is what the hook compares against at fire time.
        if native_vk:
            from withease.core.keyboard_hook import vk_to_combo_str
            mapped = vk_to_combo_str(native_vk)
            if mapped:
                return mapped
        if text:
            return f"'{text.lower()}'"
        return f"Key.{qt_key}"
