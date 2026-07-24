"""The WithEase dictation window.

A floating, always-on-top text buffer that WithEase owns.  Dictation and voice
commands are applied here (via :mod:`editor_actions`), so cursor navigation,
selection and correction are 100 % reliable.  A prominent status line shows
whether the microphone is recording or Whisper is transcribing.  "einfügen"
sends the finished text into the previously active application.

Decoupled from audio: the module feeds transcripts via :meth:`handle_transcript`
and provides ``on_insert`` / ``on_copy`` callbacks, so this file is unit-testable
without a microphone.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import commands_de as cde
import editor_actions as ea

# Status colours (match the floating chip in module.py).
_STATE = {
    "idle":         ("Bereit – Taste drücken zum Diktieren", "#2E7D32"),
    "recording":    ("🎙  Aufnahme läuft …", "#C62828"),
    "transcribing": ("⏳  Wird erkannt …", "#1565C0"),
    "error":        ("⚠  Fehler", "#C62828"),
}

_LOW_CONF_BG = QColor(255, 214, 0, 90)   # subtle yellow for uncertain words


class DictationWindow(QWidget):
    """Floating dictation buffer with voice-driven editing."""

    _transcript_sig = Signal(str)
    _state_sig = Signal(str)
    _open_sig = Signal()

    def __init__(self, on_insert: Callable[[str], None] | None = None,
                 on_copy: Callable[[str], None] | None = None,
                 t: Callable[[str], str] | None = None) -> None:
        super().__init__(parent=None)
        self._on_insert = on_insert or (lambda _txt: None)
        self._on_copy = on_copy or (lambda _txt: None)
        self._tr = t or (lambda s: s)
        self._spell_mode = False

        self.setWindowTitle("WithEase – Diktieren")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # Never steal focus from the target app when shown / updated.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(560, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._status = QLabel()
        self._status.setStyleSheet("font-weight: bold; font-size: larger;")
        layout.addWidget(self._status)

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(
            "Hier erscheint dein Diktat. Sprich Text oder Befehle wie "
            "„Cursor <Wort>“, „markiere <Wort>“, „neue Zeile“, „einfügen“.")
        layout.addWidget(self._edit, 1)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._hint)

        row = QHBoxLayout()
        self._insert_btn = QPushButton("Einfügen (in aktive App)")
        self._insert_btn.clicked.connect(lambda: self._do_insert())
        row.addWidget(self._insert_btn)
        self._copy_btn = QPushButton("Kopieren")
        self._copy_btn.clicked.connect(lambda: self._on_copy(self.text()))
        row.addWidget(self._copy_btn)
        self._close_btn = QPushButton("Schließen")
        self._close_btn.clicked.connect(self.hide)
        row.addWidget(self._close_btn)
        row.addStretch()
        layout.addLayout(row)

        self._editor = ea.Editor(self._edit)
        self._transcript_sig.connect(self._on_transcript)
        self._state_sig.connect(self._apply_state)
        self._open_sig.connect(self.open_for_dictation)
        self._apply_state("idle")

    # -- public, thread-safe API ---------------------------------------

    def handle_transcript(self, text: str) -> None:
        """Feed a recognised utterance (safe to call from a worker thread)."""
        self._transcript_sig.emit(text)

    def set_state(self, state: str) -> None:
        self._state_sig.emit(state)

    def request_open(self) -> None:
        """Show the window (safe to call from a worker thread)."""
        self._open_sig.emit()

    def text(self) -> str:
        return self._edit.toPlainText()

    def open_for_dictation(self) -> None:
        if not self.isVisible():
            self.show()
        self.raise_()

    # -- main-thread slots ---------------------------------------------

    def _apply_state(self, state: str) -> None:
        label, colour = _STATE.get(state, _STATE["idle"])
        self._status.setText(label)
        self._status.setStyleSheet(
            f"font-weight: bold; font-size: larger; color: {colour};")

    def _on_transcript(self, text: str) -> None:
        # Spell mode: the next utterance is a spelled-out word.
        if self._spell_mode:
            self._spell_mode = False
            word = cde.spell_to_text(text)
            if word:
                self._editor.insert_dictation(word)
                self._set_hint(f"eingefügt: {word}")
            else:
                self._set_hint("nichts erkannt")
            return

        cmd = cde.parse(text)
        if cmd is None:
            self._editor.insert_dictation(text)
            self._set_hint("")
            return

        # Window-level commands handled here; editing commands go to the editor.
        if cmd.kind == "insert":
            self._do_insert()
            return
        if cmd.kind == "copy":
            self._on_copy(self.text())
            self._set_hint("in die Zwischenablage kopiert")
            return
        if cmd.kind == "close":
            self.hide()
            return
        if cmd.kind == "spell_mode":
            self._spell_mode = True
            self._set_hint("Buchstabiermodus: sprich die Buchstaben")
            return

        res = self._editor.apply(cmd)
        self._set_hint(res.message)

    def _do_insert(self) -> None:
        text = self.text().strip()
        if text:
            self._on_insert(text)
            self._set_hint("in die aktive Anwendung eingefügt")

    def _set_hint(self, msg: str) -> None:
        self._hint.setText(msg or "")

    # -- confidence highlighting ---------------------------------------

    def highlight_low_confidence(self, spans: list[tuple[int, int]]) -> None:
        """Underline/tint word spans Whisper was unsure about (positions are
        offsets into the LAST inserted text; the module maps them)."""
        selections = []
        for start, end in spans:
            sel = QPlainTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(_LOW_CONF_BG)
            sel.format = fmt
            cur = QTextCursor(self._edit.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            selections.append(sel)
        self._edit.setExtraSelections(selections)
