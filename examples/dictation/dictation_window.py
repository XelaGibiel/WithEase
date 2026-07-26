"""The WithEase dictation window.

A floating, always-on-top text buffer that WithEase owns.  Dictation and voice
commands are applied here (via :mod:`editor_actions`), so cursor navigation,
selection and correction are 100 % reliable.  A prominent status line shows
whether the microphone is recording or Whisper is transcribing.  "einfügen"
sends the finished text into the previously active application and closes.

Utterances arrive with a *mode*:
  * ``"text"``    – always inserted as dictation (dictation key)
  * ``"command"`` – always parsed as a command (dedicated command key)
  * ``"auto"``    – parsed; falls back to dictation (single-key default)

A history list on the right keeps the last dictations (persisted across
sessions, newest first, capped).  When a target word occurs several times the
choices are highlighted and numbered with badges (①②③) so "nimm 9" is easy.

Decoupled from audio: the module feeds transcripts via :meth:`handle_transcript`
and provides ``on_insert`` / ``on_copy`` / ``on_history_changed`` callbacks, so
this file is unit-testable without a microphone.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
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

_LOW_CONF_BG = QColor(255, 214, 0, 90)      # subtle yellow for uncertain words
_CANDIDATE_BG = QColor(129, 199, 132, 90)   # soft green highlight for choices
_BADGE_FILL = QColor(129, 199, 132)         # calm green square (not too bright)
_BADGE_BORDER = QColor(56, 142, 60)         # darker green outline for definition
_BADGE_TEXT = QColor(20, 20, 20)            # near-black number, easy to read

_HISTORY_MAX = 20            # keep this many past dictations (FIFO)
_HISTORY_LABEL_LEN = 60      # truncate the list preview to this many chars


class _BadgeOverlay(QWidget):
    """Transparent overlay on the editor's viewport that paints numbered badges
    (①②③ …) at given document positions, for the "nimm N" choices."""

    def __init__(self, edit: QPlainTextEdit) -> None:
        super().__init__(edit.viewport())
        self._edit = edit
        self._badges: list[tuple[int, int]] = []   # (number, doc-position)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        edit.verticalScrollBar().valueChanged.connect(self._sync)
        edit.horizontalScrollBar().valueChanged.connect(self._sync)

    def set_badges(self, badges: list[tuple[int, int]]) -> None:
        self._badges = badges
        self.raise_()
        self._sync()

    def _sync(self) -> None:
        self.setGeometry(self._edit.viewport().rect())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._badges:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        d = 18.0
        for num, position in self._badges:
            cur = QTextCursor(self._edit.document())
            cur.setPosition(position)
            r = self._edit.cursorRect(cur)
            rect = QRectF(r.left() - d / 2, r.top() - d / 2, d, d)
            painter.setPen(QPen(_BADGE_BORDER, 1))
            painter.setBrush(_BADGE_FILL)
            painter.drawRoundedRect(rect, 3, 3)     # square badge, softly rounded
            painter.setPen(_BADGE_TEXT)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(num))
        painter.end()


class DictationWindow(QWidget):
    """Floating dictation buffer with voice-driven editing and a history."""

    _transcript_sig = Signal(str, str)   # (text, mode)
    _state_sig = Signal(str, str)        # (state, mode-label)
    _open_sig = Signal()

    def __init__(self, on_insert: Callable[[str], None] | None = None,
                 on_copy: Callable[[str], None] | None = None,
                 on_history_changed: Callable[[list[str]], None] | None = None,
                 on_correction: Callable[[str, str], None] | None = None,
                 history: list[str] | None = None,
                 t: Callable[[str], str] | None = None) -> None:
        super().__init__(parent=None)
        self._on_insert = on_insert or (lambda _txt: None)
        self._on_copy = on_copy or (lambda _txt: None)
        self._on_history_changed = on_history_changed or (lambda _items: None)
        self._on_correction = on_correction or (lambda _old, _new: None)
        self._tr = t or (lambda s: s)
        self._spell_mode = False

        self.setWindowTitle("WithEase – Diktieren")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # Never steal focus from the target app when shown / updated.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(620, 340)
        self.resize(780, 430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._status = QLabel()
        self._status.setStyleSheet("font-weight: bold; font-size: larger;")
        layout.addWidget(self._status)

        # --- middle: editor (left) + history (right) in a stable splitter ---
        split = QSplitter(Qt.Orientation.Horizontal)

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(
            "Hier erscheint dein Diktat. Sprich Text oder Befehle wie "
            "„Cursor vor <Wort>“, „markiere <Wort>“, „neue Zeile“, „einfügen“.")
        self._edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._edit.setMinimumWidth(280)
        self._badges = _BadgeOverlay(self._edit)
        split.addWidget(self._edit)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        hist_label = QLabel("Verlauf (zum Laden anklicken)")
        hist_label.setStyleSheet("color: palette(mid);")
        right_layout.addWidget(hist_label)
        self._history = QListWidget()
        self._history.setWordWrap(True)
        self._history.itemClicked.connect(self._load_history)
        right_layout.addWidget(self._history, 1)
        right.setMinimumWidth(150)
        right.setMaximumWidth(280)
        split.addWidget(right)

        split.setStretchFactor(0, 1)    # editor grows
        split.setStretchFactor(1, 0)    # history keeps its width
        split.setSizes([560, 200])
        layout.addWidget(split, 1)

        # Readout of what Whisper heard + what happened with it.  Fixed height,
        # no word-wrap and high-contrast so it stays readable in light *and*
        # dark themes and can never push the layout around.
        self._hint = QLabel("")
        self._hint.setWordWrap(False)
        self._hint.setFixedHeight(28)
        self._hint.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Fixed)
        self._hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._hint.setStyleSheet(
            "QLabel { color: palette(text); background: palette(base);"
            " border: 1px solid palette(mid); border-radius: 4px;"
            " padding: 3px 8px; }")
        layout.addWidget(self._hint)

        # --- buttons ---
        row = QHBoxLayout()
        self._insert_btn = QPushButton("Einfügen & Schließen")
        self._insert_btn.clicked.connect(self._do_insert)
        row.addWidget(self._insert_btn)
        self._copy_btn = QPushButton("Kopieren")
        self._copy_btn.clicked.connect(self._do_copy)
        row.addWidget(self._copy_btn)
        self._copy_close_btn = QPushButton("Kopieren & Schließen")
        self._copy_close_btn.clicked.connect(self._do_copy_and_close)
        row.addWidget(self._copy_close_btn)
        self._close_btn = QPushButton("Schließen")
        self._close_btn.clicked.connect(self._close_and_clear)
        row.addWidget(self._close_btn)
        row.addStretch()
        layout.addLayout(row)

        self._editor = ea.Editor(self._edit)
        self._load_initial_history(history or [])
        self._transcript_sig.connect(self._on_transcript)
        self._state_sig.connect(self._apply_state)
        self._open_sig.connect(self.open_for_dictation)
        self._apply_state("idle")

    # -- public, thread-safe API ---------------------------------------

    def handle_transcript(self, text: str, mode: str = "auto") -> None:
        """Feed a recognised utterance (safe to call from a worker thread)."""
        self._transcript_sig.emit(text, mode)

    def set_state(self, state: str, mode: str = "") -> None:
        self._state_sig.emit(state, mode)

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

    def _apply_state(self, state: str, mode: str = "") -> None:
        label, colour = _STATE.get(state, _STATE["idle"])
        if mode and state in ("recording", "transcribing"):
            label = f"{label}   ·   {mode}"
        self._status.setText(label)
        self._status.setStyleSheet(
            f"font-weight: bold; font-size: larger; color: {colour};")

    def _on_transcript(self, text: str, mode: str = "auto") -> None:
        # Spell mode: the next utterance is a spelled-out word (any key).
        if self._spell_mode:
            self._spell_mode = False
            word = cde.spell_to_text(text)
            if word:
                self._editor.insert_dictation(word)
                self._forward_correction()
                self._report(text, f"buchstabiert → {word}")
            else:
                self._report(text, "nichts erkannt")
            return

        # Dictation key (or explicit text mode): never interpret as a command.
        if mode == "text":
            self._editor.insert_dictation(text)
            self._forward_correction()
            self._clear_marks()
            self._report(text, "als Text eingefügt")
            return

        cmd = cde.parse(text)
        if cmd is None:
            if mode == "command":
                # Command key but nothing matched: do not dump text into buffer.
                self._report(text, "Befehl nicht erkannt")
                return
            self._editor.insert_dictation(text)
            self._forward_correction()
            self._clear_marks()
            self._report(text, "als Text eingefügt")
            return

        # Window-level commands handled here; editing commands go to the editor.
        if cmd.kind == "insert":
            self._do_insert()
            self._report(text, "Befehl: in aktive App eingefügt")
            return
        if cmd.kind == "copy":
            self._do_copy()
            self._report(text, "Befehl: in die Zwischenablage kopiert")
            return
        if cmd.kind == "close":
            self._report(text, "Befehl: Fenster schließen")
            self._close_and_clear()
            return
        if cmd.kind == "spell_mode":
            self._spell_mode = True
            self._report(text, "Buchstabiermodus: sprich die Buchstaben")
            return
        if cmd.kind == "spell_inline":
            word = cde.spell_to_text(cmd.data.get("text", ""))
            if word:
                self._editor.insert_dictation(word)
                self._forward_correction()
                self._report(text, f"buchstabiert → {word}")
            else:
                self._report(text, "nichts erkannt")
            return

        res = self._editor.apply(cmd)
        self._forward_correction()      # "ersetze A durch B" learns here too
        if res.status == "ambiguous" and res.matches:
            legend = self._mark_candidates(res.matches)
            self._report(text, legend)
        else:
            self._clear_marks()
            self._report(text, res.message or f"Befehl: {cmd.kind}")

    def _do_insert(self) -> None:
        text = self.text().strip()
        if not text:
            return
        self._on_insert(text)
        # "Einfügen" hands the text over and gets out of the way: the window
        # archives + clears + closes in the same moment, so the next dictation
        # starts fresh without an extra "schließen".
        self._close_and_clear()

    def _do_copy(self) -> None:
        text = self.text().strip()
        if text:
            self._on_copy(text)
            self._archive()
            self._set_hint("in die Zwischenablage kopiert")

    def _do_copy_and_close(self) -> None:
        text = self.text().strip()
        if text:
            self._on_copy(text)
        self._close_and_clear()

    # -- history & buffer lifecycle ------------------------------------

    def _load_initial_history(self, items: list[str]) -> None:
        for text in items[:_HISTORY_MAX]:      # stored newest-first
            self._add_history_item(text)

    def _add_history_item(self, text: str) -> None:
        label = " ".join(text.split())
        if len(label) > _HISTORY_LABEL_LEN:
            label = label[:_HISTORY_LABEL_LEN - 1] + "…"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, text)
        item.setToolTip(text)
        self._history.addItem(item)

    def _archive(self) -> None:
        """Store the current buffer as a history entry (newest on top, no
        consecutive duplicates, capped FIFO), then persist."""
        text = self.text().strip()
        if not text:
            return
        if self._history.count() and \
                self._history.item(0).data(Qt.ItemDataRole.UserRole) == text:
            return
        label = " ".join(text.split())
        if len(label) > _HISTORY_LABEL_LEN:
            label = label[:_HISTORY_LABEL_LEN - 1] + "…"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, text)
        item.setToolTip(text)
        self._history.insertItem(0, item)
        while self._history.count() > _HISTORY_MAX:     # drop the oldest
            self._history.takeItem(self._history.count() - 1)
        self._persist_history()

    def _persist_history(self) -> None:
        items = [self._history.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self._history.count())]
        self._on_history_changed(items)

    def _clear_marks(self) -> None:
        self._edit.setExtraSelections([])
        self._badges.set_badges([])

    def _clear_buffer(self) -> None:
        self._edit.clear()
        self._clear_marks()
        # Fresh editor state (drops any pending selection / correction).
        self._editor = ea.Editor(self._edit)

    def _close_and_clear(self) -> None:
        self._archive()
        self._clear_buffer()
        self._set_hint("")
        self.hide()

    def _load_history(self, item: QListWidgetItem) -> None:
        text = item.data(Qt.ItemDataRole.UserRole)
        if not text:
            return
        # Keep whatever is currently in the buffer before replacing it.
        self._archive()
        self._edit.setPlainText(text)
        self._editor = ea.Editor(self._edit)
        self._clear_marks()
        cur = self._edit.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self._edit.setTextCursor(cur)
        self._set_hint("aus dem Verlauf geladen")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # The window is reusable: hide + clear instead of destroying it.
        event.ignore()
        self._close_and_clear()

    def _forward_correction(self) -> None:
        """If the last edit replaced a word, let the error memory learn it."""
        pair = self._editor.last_correction
        if pair:
            self._editor.last_correction = None
            self._on_correction(pair[0], pair[1])

    def _set_hint(self, msg: str) -> None:
        self._hint.setText(msg or "")

    def _report(self, raw: str, outcome: str) -> None:
        """Always show what Whisper heard and what was done with it – makes it
        obvious whether an utterance was taken as a command or as text."""
        raw = " ".join((raw or "").split())
        if len(raw) > 60:            # keep the „→ Ergebnis“ part visible
            raw = raw[:59] + "…"
        self._hint.setText(f"erkannt: „{raw}“   →   {outcome}")

    # -- numbered "nimm N" candidates ----------------------------------

    def _mark_candidates(self, matches: list[tuple[int, int]]) -> str:
        """Highlight the „nimm N“ choices in the text, paint numbered badges
        (choice 1..N in document order) and build a legend for the readout."""
        selections = []
        for start, end in matches:
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(_CANDIDATE_BG)
            sel.format = fmt
            cur = QTextCursor(self._edit.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            selections.append(sel)
        self._edit.setExtraSelections(selections)
        self._badges.set_badges([(i, s) for i, (s, _e) in enumerate(matches, 1)])

        doc = self.text()
        parts = []
        for i, (start, end) in enumerate(matches, 1):
            snippet = doc[start:min(len(doc), end + 12)].replace("\n", " ").strip()
            parts.append(f"{i}: …{snippet}…")
        return f"{len(matches)} Treffer – sag „nimm N“:   " + "   ".join(parts)

    # -- confidence highlighting ---------------------------------------

    def highlight_low_confidence(self, spans: list[tuple[int, int]]) -> None:
        """Underline/tint word spans Whisper was unsure about (positions are
        offsets into the LAST inserted text; the module maps them)."""
        selections = []
        for start, end in spans:
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(_LOW_CONF_BG)
            sel.format = fmt
            cur = QTextCursor(self._edit.document())
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            selections.append(sel)
        self._edit.setExtraSelections(selections)
