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

import re
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
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import commands_de as cde
import correction as co
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


class CorrectionDialog(QDialog):
    """A Dragon-style correction window.

    Shows the mis-recognised text and an editable field the user can *type*
    (self-input, so a repeated mis-hearing is impossible) or re-speak into.
    Fully voice-operable: while it is open, spoken text fills the field, and
    "übernehmen" / "abbrechen" confirm or cancel hands-free."""

    _CONFIRM = {"übernehmen", "uebernehmen", "fertig", "ok", "okay", "passt",
                "korrigieren", "korrektur übernehmen"}
    _CANCEL = {"abbrechen", "abbruch", "verwerfen", "schließen", "schliessen"}

    def __init__(self, current_text: str,
                 on_apply: Callable[[str], None],
                 on_cancel: Callable[[], None],
                 suggestions: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_apply = on_apply
        self._on_cancel = on_cancel
        self._suggestions = list(suggestions or [])
        self._done = False
        self.setWindowTitle("Korrektur")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Bisher erkannt:"))
        old = QLabel(current_text or "—")
        old.setWordWrap(True)
        old.setStyleSheet("font-weight: bold; color: #E04A4A;")
        layout.addWidget(old)

        layout.addWidget(QLabel("Richtige Version (tippen oder sprechen):"))
        self._field = QLineEdit(current_text)
        self._field.selectAll()
        self._field.returnPressed.connect(self._apply)
        layout.addWidget(self._field)

        # Numbered alternative suggestions (Dragon-style): click, or say "nimm N".
        if self._suggestions:
            layout.addWidget(QLabel("Vorschläge (anklicken oder „nimm N“ sagen):"))
            for i, sug in enumerate(self._suggestions, 1):
                btn = QPushButton(f"{i}    {sug}")
                btn.setStyleSheet("text-align: left; padding: 4px 8px;")
                btn.clicked.connect(lambda _=False, v=sug: self._pick_value(v))
                layout.addWidget(btn)

        hint = QLabel(
            "Tippe die Korrektur, oder drücke die Diktier-Taste und sprich die "
            "richtige Version. Dann „Übernehmen“ (oder sag „übernehmen“ / "
            "„abbrechen“).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(windowText);")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(self._cancel)
        row.addWidget(cancel_btn)
        apply_btn = QPushButton("Übernehmen")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply)
        row.addWidget(apply_btn)
        layout.addLayout(row)

    # -- voice routing (called by the window while this dialog is open) --

    def set_spoken(self, text: str) -> None:
        # Whisper appends a sentence period to a single spoken word ("Erde.");
        # a correction word should not carry it.
        self._field.setText((text or "").strip().rstrip(" .,;:!?…"))
        self._field.selectAll()

    def handle_voice(self, text: str) -> None:
        key = (text or "").strip().lower().strip(" .,!?…")
        if key in self._CONFIRM:
            self._apply()
            return
        if key in self._CANCEL:
            self._cancel()
            return
        cmd = cde.parse(text)
        if cmd is not None and cmd.kind == "pick":     # "nimm zwei"
            self.pick(int(cmd.data.get("n", 0)))
            return
        self.set_spoken(text)

    def pick(self, n: int) -> None:
        if 1 <= n <= len(self._suggestions):
            self._pick_value(self._suggestions[n - 1])

    def _pick_value(self, value: str) -> None:
        self._field.setText(value)
        self._apply()

    def result_text(self) -> str:
        return self._field.text()

    # -- outcome --------------------------------------------------------

    def _apply(self) -> None:
        if self._done:
            return
        self._done = True
        text = self._field.text().strip()
        self.close()
        self._on_apply(text)

    def _cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        self._on_cancel()


class CommandCheatSheet(QDialog):
    """A quick reference of the available voice commands."""

    _HTML = """
    <h3>Sprachbefehle</h3>
    <p><b>Diktat:</b> einfach sprechen · „neue Zeile“ · „neuer Absatz“ ·
    „Punkt“ / „Komma“ / „Fragezeichen“ · „großschreiben“ / „kleinschreiben“</p>
    <p><b>Navigation:</b> „Cursor vor &lt;Wort&gt;“ · „hinter &lt;Wort&gt;“ ·
    „an den Anfang“ · „ans Ende“</p>
    <p><b>Auswahl:</b> „markiere &lt;Wort/Text&gt;“ · „markiere von A bis B“ ·
    „markiere diesen Satz“ · „markiere diesen Absatz“ · „markiere alles“ ·
    bei mehreren Treffern: „nimm 1“ … „nimm 9“</p>
    <p><b>Löschen:</b> „lösche &lt;Wort&gt;“ · „lösche das“ ·
    „lösche diesen Satz“ · „alles löschen“</p>
    <p><b>Korrektur:</b> „korrigiere das“ · „korrigiere &lt;Wort&gt;“ ·
    „ersetze A durch B“ · „buchstabiere Anton Berta …“ ·
    „rückgängig“ / „wiederholen“</p>
    <p><b>Korrekturfenster:</b> tippen oder sprechen · „nimm N“ für einen
    Vorschlag · „übernehmen“ / „abbrechen“</p>
    <p><b>Fenster:</b> „einfügen“ / „Text einfügen“ · „kopieren“ /
    „Text kopieren“ · „Fenster schließen“</p>
    <p><b>Wörtlich einfügen (statt Befehl):</b> „wörtlich &lt;Text&gt;“</p>
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprachbefehle")
        self.resize(480, 560)
        layout = QVBoxLayout(self)
        view = QTextBrowser()
        view.setHtml(self._HTML)
        layout.addWidget(view, 1)
        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class DictationWindow(QWidget):
    """Floating dictation buffer with voice-driven editing and a history."""

    _transcript_sig = Signal(str, str, list)   # (text, mode, low-conf words)
    _state_sig = Signal(str, str)              # (state, mode-label)
    _open_sig = Signal()
    _target_sig = Signal(str)                  # target app name (thread-safe)

    def __init__(self, on_insert: Callable[[str], None] | None = None,
                 on_copy: Callable[[str], None] | None = None,
                 on_history_changed: Callable[[list[str]], None] | None = None,
                 on_correction: Callable[[str, str], None] | None = None,
                 on_suggest: Callable[[str], list[str]] | None = None,
                 on_reselect_target: Callable[[], None] | None = None,
                 on_confirm_words: Callable[[list], None] | None = None,
                 on_geometry_changed: Callable[[list], None] | None = None,
                 geometry: list | None = None,
                 history: list[str] | None = None,
                 t: Callable[[str], str] | None = None) -> None:
        super().__init__(parent=None)
        self._on_insert = on_insert or (lambda _txt: True)
        self._on_copy = on_copy or (lambda _txt: None)
        self._on_history_changed = on_history_changed or (lambda _items: None)
        self._on_correction = on_correction or (lambda _old, _new: None)
        self._on_suggest = on_suggest or (lambda _wrong: [])
        self._on_reselect_target = on_reselect_target or (lambda: None)
        self._on_confirm_words = on_confirm_words or (lambda _words: None)
        self._on_geometry_changed = on_geometry_changed or (lambda _g: None)
        self._restore_geometry = geometry
        self._pending_low_words: list[str] = []   # flagged-but-still-here words
        self._tr = t or (lambda s: s)
        self._spell_mode = False
        self._correction_dialog: CorrectionDialog | None = None

        self.setWindowTitle("WithEase – Diktieren")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # The window may take focus so the caret sits in the edit field right
        # away; the target app is remembered *before* the window opens, so
        # "einfügen" still pastes into the right place.
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
        hist_label.setStyleSheet("color: palette(windowText);")
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

        # Which app "einfügen" will paste into.
        self._target_label = QLabel("")
        self._target_label.setStyleSheet("color: palette(windowText);")
        layout.addWidget(self._target_label)

        # Small toolbar: undo/redo, re-pick target app, command help.
        tools = QHBoxLayout()
        undo_btn = QPushButton("↶ Rückgängig")
        undo_btn.clicked.connect(lambda: self._editor.te.undo())
        tools.addWidget(undo_btn)
        redo_btn = QPushButton("↷ Wiederholen")
        redo_btn.clicked.connect(lambda: self._editor.te.redo())
        tools.addWidget(redo_btn)
        self._reselect_btn = QPushButton("🎯 Ziel-App wählen")
        self._reselect_btn.clicked.connect(lambda: self._on_reselect_target())
        tools.addWidget(self._reselect_btn)
        help_btn = QPushButton("❓ Befehle")
        help_btn.clicked.connect(self._show_cheatsheet)
        tools.addWidget(help_btn)
        tools.addStretch()
        layout.addLayout(tools)

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
        self._target_sig.connect(self._apply_target)
        self._apply_state("idle")
        self._apply_target("")
        if self._restore_geometry and len(self._restore_geometry) == 4:
            try:
                self.setGeometry(*[int(v) for v in self._restore_geometry])
            except Exception:
                pass

    # -- public, thread-safe API ---------------------------------------

    def handle_transcript(self, text: str, mode: str = "auto",
                          low_words: list | None = None) -> None:
        """Feed a recognised utterance (safe to call from a worker thread)."""
        self._transcript_sig.emit(text, mode, list(low_words or []))

    def set_state(self, state: str, mode: str = "") -> None:
        self._state_sig.emit(state, mode)

    def set_target(self, name: str) -> None:
        """Set the app name shown for „einfügen" (safe from a worker thread)."""
        self._target_sig.emit(name or "")

    def request_open(self) -> None:
        """Show the window (safe to call from a worker thread)."""
        self._open_sig.emit()

    def text(self) -> str:
        return self._edit.toPlainText()

    def open_for_dictation(self) -> None:
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        # Focus the edit field so the caret is there immediately.  Keep any
        # existing selection (e.g. a word the user marked to overwrite); only
        # move the caret to the end when nothing is selected.
        cur = self._edit.textCursor()
        if not cur.hasSelection():
            cur.movePosition(QTextCursor.MoveOperation.End)
            self._edit.setTextCursor(cur)
        self._edit.setFocus()

    # -- main-thread slots ---------------------------------------------

    def _apply_state(self, state: str, mode: str = "") -> None:
        label, colour = _STATE.get(state, _STATE["idle"])
        if mode and state in ("recording", "transcribing"):
            label = f"{label}   ·   {mode}"
        self._status.setText(label)
        self._status.setStyleSheet(
            f"font-weight: bold; font-size: larger; color: {colour};")

    def _on_transcript(self, text: str, mode: str = "auto",
                       low_words: list | None = None) -> None:
        # Correction window open: route speech into it (fill field / confirm).
        if self._correction_dialog is not None:
            if self._correction_dialog.isVisible():
                self._correction_dialog.handle_voice(text)
                self._report(text, "→ Korrekturfenster")
                return
            self._correction_dialog = None   # it was closed → fall through

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
            self._highlight_low_words(low_words)
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
            self._highlight_low_words(low_words)
            self._report(text, "als Text eingefügt")
            return

        # Window-level commands handled here; editing commands go to the editor.
        if cmd.kind == "insert":
            self._do_insert()   # sets its own hint (success closes, else fallback)
            return
        if cmd.kind == "copy":
            self._do_copy()
            self._report(text, "Befehl: in die Zwischenablage kopiert")
            return
        if cmd.kind == "close":
            self._report(text, "Befehl: Fenster schließen")
            self._close_and_clear()
            return
        if cmd.kind == "reselect_target":
            self._on_reselect_target()
            self._report(text, "Ziel-App wird neu gewählt …")
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
        if res.status == "awaiting_dictation":
            # "korrigiere …" selected the target → open the correction window.
            self._clear_marks()
            self._open_correction(self._edit.textCursor().selectedText())
            self._report(text, "Korrekturfenster geöffnet")
        elif res.status == "ambiguous" and res.matches:
            legend = self._mark_candidates(res.matches)
            self._report(text, legend)
        else:
            self._clear_marks()
            self._report(text, res.message or f"Befehl: {cmd.kind}")

    def _do_insert(self) -> None:
        text = self.text().strip()
        if not text:
            return
        ok = self._on_insert(text)
        if ok is False:
            # Target app gone/invalid: text was put on the clipboard instead.
            self._set_hint("konnte nicht einfügen – Text liegt in der "
                           "Zwischenablage (Strg+V)")
            return
        self._confirm_low_words()   # accepted unchanged → learn they were right
        # "Einfügen" hands the text over and gets out of the way: the window
        # archives + clears + closes in the same moment, so the next dictation
        # starts fresh without an extra "schließen".
        self._close_and_clear()

    def _confirm_low_words(self) -> None:
        """Words that were flagged as uncertain but are still present (accepted
        unchanged) are confirmed as correct, so they stop being flagged."""
        if not self._pending_low_words:
            return
        tokens = {m.group().casefold()
                  for m in re.finditer(r"\w+", self.text(), re.UNICODE)}
        still = [w for w in self._pending_low_words
                 if w.casefold() in tokens]
        if still:
            self._on_confirm_words(still)
        self._pending_low_words = []

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
        self._pending_low_words = []
        # Fresh editor state (drops any pending selection / correction).
        self._editor = ea.Editor(self._edit)

    def _close_and_clear(self) -> None:
        self._save_geometry()
        self._archive()
        self._clear_buffer()
        self._set_hint("")
        self.hide()

    def _save_geometry(self) -> None:
        g = self.geometry()
        self._on_geometry_changed([g.x(), g.y(), g.width(), g.height()])

    def _apply_target(self, name: str) -> None:
        name = (name or "").strip()
        if len(name) > 60:
            name = name[:59] + "…"
        self._target_label.setText(
            f"→ „Einfügen“ fügt ein in:  {name}" if name
            else "→ „Einfügen“: (noch keine Ziel-App gemerkt)")

    def _highlight_low_words(self, low_words: list | None) -> None:
        """Tint words Whisper was unsure about (confidence heatmap).  Matches by
        word text inside the just-inserted span, so it survives smart spacing."""
        self._clear_marks()
        if not low_words or self._editor._last_insert is None:
            return
        start, end = self._editor._last_insert
        chunk = self.text()[start:end]
        wanted = {w.strip().casefold() for w in low_words if w and w.strip()}
        if not wanted:
            return
        spans, matched = [], []
        for m in re.finditer(r"\w+", chunk, re.UNICODE):
            if m.group().casefold() in wanted:
                spans.append((start + m.start(), start + m.end()))
                matched.append(m.group())
        if spans:
            self.highlight_low_confidence(spans)
        # Remember flagged words so that, if accepted unchanged, we can learn.
        self._pending_low_words.extend(matched)

    def _show_cheatsheet(self) -> None:
        dlg = CommandCheatSheet(parent=self)
        dlg.show()
        dlg.raise_()

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

    # -- Dragon-style correction window --------------------------------

    def _open_correction(self, old_text: str) -> None:
        dlg = CorrectionDialog(
            old_text or "", on_apply=self._apply_correction,
            on_cancel=self._cancel_correction,
            suggestions=self._collect_suggestions(old_text), parent=self)
        self._correction_dialog = dlg
        # Any close path (X, Escape, done) must release the routing lock.
        dlg.finished.connect(lambda _r: self._release_correction(dlg))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg._field.setFocus()

    def _collect_suggestions(self, wrong: str) -> list[str]:
        """Alternatives for the correction window: learned correction + glossary
        (from the module), plus similar words in the buffer and casing variants,
        ranked by similarity and de-duplicated."""
        wrong = (wrong or "").strip()
        if not wrong:
            return []
        out: list[str] = []
        try:
            out += list(self._on_suggest(wrong) or [])
        except Exception:
            out = []
        pool = [w for _s, _e, w in self._editor._tokens()]
        pool += [wrong[:1].upper() + wrong[1:], wrong.lower(), wrong.upper()]
        out += co.suggest_alternatives(wrong, pool, limit=9)
        result, seen = [], set()
        for sug in out:
            sug = (sug or "").strip()
            if not sug or sug == wrong or sug.casefold() in seen:
                continue
            seen.add(sug.casefold())
            result.append(sug)
            if len(result) >= 9:
                break
        return result

    def _apply_correction(self, new_text: str) -> None:
        self._correction_dialog = None
        new = (new_text or "").strip()
        if new:
            # The target is still selected in the buffer → replace it (and learn,
            # since a correction command set the awaiting-correction flag).
            self._editor.insert_dictation(new)
            self._forward_correction()
            self._set_hint(f"korrigiert zu: {new}")
        else:
            self._set_hint("Korrektur abgebrochen")

    def _cancel_correction(self) -> None:
        self._correction_dialog = None
        self._set_hint("Korrektur abgebrochen")

    def _release_correction(self, dlg) -> None:
        # Fired on any dialog close (incl. window X / Escape) so routing resumes.
        if self._correction_dialog is dlg:
            self._correction_dialog = None

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
