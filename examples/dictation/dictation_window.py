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
import sys
import uuid
from typing import Callable

from PySide6.QtCore import QEvent, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# The event bus is how the cheat sheet's microphone asks for one dictation.
# Imported defensively so this file still runs standalone (see the docstring).
try:
    from withease.core.event_bus import bus
except Exception:                                   # pragma: no cover
    class _NoBus:
        def publish(self, *_a, **_k) -> None: ...
        def subscribe(self, *_a, **_k) -> None: ...
        def unsubscribe(self, *_a, **_k) -> None: ...

    bus = _NoBus()

import commands_de as cde
from dict_i18n import t as _t
import correction as co
import editor_actions as ea


def _diff_html(original: str, result: str) -> str:
    """HTML of ``result`` with the words that differ from ``original`` marked
    green, so a KI-Aktion's changes are easy to see and compare."""
    import difflib
    import html as _html

    def toks(t: str) -> list[str]:
        return re.findall(r"\S+|\n", t)

    sm = difflib.SequenceMatcher(None, toks(original), toks(result))
    b = toks(result)
    parts: list[str] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        for tok in b[j1:j2]:
            if tok == "\n":
                parts.append("<br>")
                continue
            esc = _html.escape(tok)
            if tag != "equal":
                esc = ('<span style="background:#2e7d32; color:#fff; '
                       'border-radius:3px; padding:0 2px;">' + esc + "</span>")
            parts.append(esc + " ")
    return "".join(parts)


def _wrap_tip(text: str) -> str:
    """``ui_utils.wrap_tooltip`` with a fallback for an OLDER core.

    Without it Qt lays a tool-tip out as ONE line: a two-sentence explanation
    then stretches from screen edge to screen edge and is unreadable.  Every
    setToolTip in this file goes through here."""
    try:
        from withease.gui.ui_utils import wrap_tooltip
        return wrap_tooltip(text)
    except Exception:
        return text


def _mark_danger(button):
    """``ui_utils.mark_danger`` with a fallback for an OLDER core.

    An add-on is installed independently of the program, so a missing helper
    must never be more than a missing tint."""
    try:
        from withease.gui.ui_utils import mark_danger
        return mark_danger(button)
    except Exception:
        return button


def _show_undo(widget, text: str, on_undo) -> bool:
    """``widgets.undo_bar.show_undo`` with a fallback for an OLDER core.

    False means the bar is not available and the caller must ask first – no
    path may delete with no way back."""
    try:
        from withease.gui.widgets.undo_bar import show_undo
        return show_undo(widget, text, on_undo) is not None
    except Exception:
        return False


class _AiPreview(QDialog):
    """Preview a KI-Aktion result before keeping it.

    Three views to switch between – the result with changes highlighted, the
    untouched original, or both side by side – and the chosen view plus the
    window size/position are remembered for next time (via QSettings)."""

    _MODES = ("result", "original", "side")

    def __init__(self, original: str, result: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KI-Vorschau")
        self._original = original
        self._result = result
        self._store = QSettings("WithEase", "AiPreview")

        geo = self._store.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)          # remembered size/position
        else:
            self.resize(660, 480)

        layout = QVBoxLayout(self)
        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color: palette(windowText);")
        layout.addWidget(self._info)
        self._view = QTextBrowser()          # single view: Ergebnis / Original
        layout.addWidget(self._view, 1)
        # Side-by-side: a real splitter so the divider can be dragged; a wider,
        # accented handle makes it clearly grabbable.
        self._split = QSplitter(Qt.Orientation.Horizontal)
        self._split.setHandleWidth(10)
        self._split.setStyleSheet(
            "QSplitter::handle:horizontal { width: 10px; margin: 2px 4px;"
            " border-radius: 4px; background: palette(mid); }"
            "QSplitter::handle:horizontal:hover { background: palette(dark); }")
        self._left = QTextBrowser()
        self._right = QTextBrowser()
        self._split.addWidget(self._left)
        self._split.addWidget(self._right)
        self._split.setCollapsible(0, False)
        self._split.setCollapsible(1, False)
        layout.addWidget(self._split, 1)
        self._split.hide()

        row = QHBoxLayout()
        # View switch: Ergebnis (diff) · Original · Nebeneinander (side by side).
        self._mode_btns: dict[str, QPushButton] = {}
        for mode, label in (("result", "Ergebnis"),
                            ("original", "Original"),
                            ("side", "Nebeneinander")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            # Checked buttons render bold (theme QSS), which is wider than the
            # size hint computed with the normal font – reserve the bold width
            # so the label is never clipped when active.
            _bold = QFont(b.font())
            _bold.setBold(True)
            b.setMinimumWidth(QFontMetrics(_bold).horizontalAdvance(label) + 34)
            b.clicked.connect(lambda _c=False, m=mode: self._set_mode(m))
            row.addWidget(b)
            self._mode_btns[mode] = b
        row.addSpacing(12)
        self._highlight = bool(self._store.value("highlight", True, type=bool))
        self._hl_cb = QCheckBox(_t("ai.highlight"))
        self._hl_cb.setChecked(self._highlight)
        self._hl_cb.setToolTip(_wrap_tip(_t("ai.highlight.hint")))
        self._hl_cb.toggled.connect(self._on_highlight)
        row.addWidget(self._hl_cb)
        row.addStretch()
        discard = QPushButton("Verwerfen")
        discard.clicked.connect(self.reject)
        row.addWidget(discard)
        keep = QPushButton(_t("ai.apply"))
        keep.setDefault(True)
        keep.clicked.connect(self.accept)
        row.addWidget(keep)
        layout.addLayout(row)

        mode = self._store.value("mode", "result")
        if mode not in self._MODES:
            mode = "result"
        self._set_mode(mode)                    # remembered view

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._store.setValue("mode", mode)
        for m, b in self._mode_btns.items():
            b.setChecked(m == mode)
        self._render()

    def _on_highlight(self, on: bool) -> None:
        self._highlight = bool(on)
        self._store.setValue("highlight", self._highlight)
        self._render()

    def _result_html(self) -> str:
        """The result – with green change-highlights when the option is on,
        otherwise plain text."""
        if self._highlight:
            return _diff_html(self._original, self._result)
        import html as _html
        return _html.escape(self._result).replace("\n", "<br>")

    def _render(self) -> None:
        import html as _html
        # The highlight option only affects the result views, not the original.
        self._hl_cb.setEnabled(self._mode != "original")
        if self._highlight and self._mode != "original":
            self._info.setText(
                _t("ai.result.diff"))
        else:
            self._info.setText(_t("ai.result"))
        side = (self._mode == "side")
        self._split.setVisible(side)
        self._view.setVisible(not side)
        if side:
            self._left.setHtml(
                "<b>Original</b><br><br>"
                + _html.escape(self._original).replace("\n", "<br>"))
            self._right.setHtml(
                "<b>Ergebnis</b><br><br>" + self._result_html())
        elif self._mode == "original":
            self._view.setHtml(
                _html.escape(self._original).replace("\n", "<br>"))
        else:
            self._view.setHtml(self._result_html())

    def _save_geometry(self) -> None:
        self._store.setValue("geometry", self.saveGeometry())

    def accept(self) -> None:          # type: ignore[override]
        self._save_geometry()
        super().accept()

    def reject(self) -> None:          # type: ignore[override]
        self._save_geometry()
        super().reject()

# Status colours (match the floating chip in module.py).
_STATE = {
    "idle":         ("state.idle", "#2E7D32"),
    "recording":    ("state.recording", "#C62828"),
    "transcribing": ("state.transcribing", "#1565C0"),
    "error":        ("state.error", "#C62828"),
}

_LOW_CONF_BG = QColor(255, 214, 0, 90)      # subtle yellow for uncertain words
_CANDIDATE_BG = QColor(129, 199, 132, 90)   # soft green highlight for choices
_BADGE_FILL = QColor(129, 199, 132)         # calm green square (not too bright)
_BADGE_BORDER = QColor(56, 142, 60)         # darker green outline for definition
_BADGE_TEXT = QColor(20, 20, 20)            # near-black number, easy to read

_HISTORY_MAX = 20            # keep this many past dictations (FIFO)
_HISTORY_LABEL_LEN = 60      # truncate the list preview to this many chars


def _win_force_foreground(hwnd: int) -> None:
    """Force a window to the foreground on Windows, bypassing the foreground
    lock (a background app can't normally steal focus).  Without this the
    dictation window shows a caret but the keyboard doesn't reach it until the
    user clicks in."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        for fn in ("SetForegroundWindow", "SetActiveWindow", "BringWindowToTop"):
            getattr(u, fn).argtypes = [wintypes.HWND]
        u.AttachThreadInput.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        win = wintypes.HWND(hwnd)
        fg = u.GetForegroundWindow()
        cur = k.GetCurrentThreadId()
        fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur:
            attached = bool(u.AttachThreadInput(fg_tid, cur, True))
        u.BringWindowToTop(win)
        u.SetForegroundWindow(win)
        u.SetActiveWindow(win)
        if attached:
            u.AttachThreadInput(fg_tid, cur, False)
    except Exception:
        pass


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
        self.setWindowTitle(_t("corr.title"))
        self.setMinimumWidth(440)
        # Sit above the always-on-top dictation window and grab the keyboard
        # focus (modal) so the field can be edited with the keyboard/mouse right
        # away, without having to click into it first.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(QLabel(_t("corr.heard")))
        old = QLabel(current_text or "—")
        old.setWordWrap(True)
        old.setStyleSheet("font-weight: bold; color: #E04A4A;")
        layout.addWidget(old)

        layout.addWidget(QLabel(_t("corr.correct")))
        self._field = QLineEdit(current_text)
        self._field.selectAll()
        self._field.returnPressed.connect(self._apply)
        layout.addWidget(self._field)

        # Numbered alternative suggestions (Dragon-style): click, or say "nimm N".
        if self._suggestions:
            layout.addWidget(QLabel(_t("corr.suggestions")))
            for i, sug in enumerate(self._suggestions, 1):
                btn = QPushButton(f"{i}    {sug}")
                btn.setStyleSheet("text-align: left; padding: 4px 8px;")
                btn.clicked.connect(lambda _=False, v=sug: self._pick_value(v))
                layout.addWidget(btn)

        hint = QLabel(_t("corr.help"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(windowText);")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        cancel_btn = QPushButton(_t("corr.cancel"))
        cancel_btn.clicked.connect(self._cancel)
        row.addWidget(cancel_btn)
        apply_btn = QPushButton(_t("corr.apply"))
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply)
        row.addWidget(apply_btn)
        layout.addLayout(row)

    # -- voice routing (called by the window while this dialog is open) --

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # Focus the field once the dialog is actually shown, so the word can be
        # edited (arrow keys) right away.
        self._field.setFocus()
        self._field.selectAll()

    def focus_field(self) -> None:
        self.raise_()
        self.activateWindow()
        self._field.setFocus()
        self._field.selectAll()

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
    """Searchable reference of the voice commands.

    Built from ``commands_de.CHEAT_SHEET`` – the same file that defines the
    grammar – so the list cannot drift away from what actually works, the way
    the previous hand-written HTML block could.

    Laid out as ONE table with a fixed command column rather than one table per
    group: with a table each, every group sized its own columns, so the
    explanations started at a different place in every section and the eye had
    to find the column again after each heading.  One table means one column
    edge down the whole page – which is what makes a list of forty entries
    scannable at all.  Alternating row tints do the same job line by line.
    """

    # Filled in once the online manual exists; until then the button is simply
    # not shown, so nothing points at a dead link.
    MANUAL_URL = ""

    # The dictation module answers from its transcription WORKER thread, so the
    # bus callback must not touch widgets directly – it hops onto the GUI
    # thread through this signal first.
    _captured = Signal(str, str)        # token, text

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprachbefehle")
        # Wide enough for the two columns side by side, and only as tall as the
        # screen allows – the footer must never be pushed out of view.
        from PySide6.QtWidgets import QApplication as _QApp
        screen = _QApp.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        self.resize(760, min(760, avail.height() - 80) if avail else 700)
        self.setMinimumSize(520, 320)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(_t("cheat.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(_t("cheat.search"))
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._render)
        # Searching a list of voice commands by typing would be an odd demand
        # in this program, so the search takes dictation too – the same
        # one-shot capture the settings search uses.
        self._mic = QPushButton("🎤")
        self._mic.setToolTip(_wrap_tip(_t("cheat.mic")))
        self._mic.setAccessibleName(_t("cheat.mic"))
        self._mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic.clicked.connect(self._on_mic)
        self._capture_token = ""
        self._captured.connect(self._apply_capture)
        bus.subscribe("dictation.capture_result", self._on_capture_result)
        self.destroyed.connect(
            lambda: bus.unsubscribe("dictation.capture_result",
                                    self._on_capture_result))
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_row.addWidget(self._filter, 1)
        search_row.addWidget(self._mic)
        layout.addLayout(search_row)

        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(True)
        layout.addWidget(self._view, 1)

        footer = QHBoxLayout()
        self._count = QLabel("")
        footer.addWidget(self._count)
        footer.addStretch(1)
        if self.MANUAL_URL:
            manual = QPushButton(_t("cheat.manual"))
            manual.setToolTip(_wrap_tip(self.MANUAL_URL))
            manual.clicked.connect(self._open_manual)
            footer.addWidget(manual)
        close_btn = QPushButton(_t("cheat.close"))
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._render("")

    # -- voice search ----------------------------------------------------

    def _set_listening(self, on: bool) -> None:
        self._mic.setText("⏹" if on else "🎤")
        self._mic.setToolTip(_wrap_tip(_t("cheat.mic.stop") if on else _t("cheat.mic")))
        self._filter.setPlaceholderText(
            "Sprich jetzt …" if on else _t("cheat.search"))

    def _on_mic(self) -> None:
        # Second click = stop.  The button starts the recording, so it has to
        # be able to end it too.
        if self._capture_token:
            bus.publish("dictation.capture_stop", token=self._capture_token)
            return
        token = self._capture_token = uuid.uuid4().hex
        self._set_listening(True)
        bus.publish("dictation.capture_request", token=token)

        def give_up() -> None:
            if self._capture_token == token:
                self._capture_token = ""
                self._set_listening(False)

        QTimer.singleShot(120_000, give_up)

    def _on_capture_result(self, token: str = "", text: str = "",
                           **_: object) -> None:
        self._captured.emit(token or "", text or "")

    def _apply_capture(self, token: str, text: str) -> None:
        if token != self._capture_token:
            return                      # not our request
        self._capture_token = ""
        self._set_listening(False)
        if text:
            self._filter.setText(text)

    def _open_manual(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self.MANUAL_URL))

    def _colors(self) -> dict:
        """Colours taken from the palette, so the sheet follows light/dark."""
        pal = self.palette()
        text = pal.color(pal.ColorRole.WindowText)
        base = pal.color(pal.ColorRole.Base)
        dark = base.lightness() < 128
        # A SOLID tint, not a translucent one: Qt's rich text honours a cell's
        # `bgcolor` attribute for the whole cell, while a CSS background-color
        # is only painted behind the text itself – which left half-striped rows.
        alt = base.lighter(118) if dark else base.darker(105)
        rule = QColor(255, 255, 255, 46) if dark else QColor(0, 0, 0, 40)
        muted = QColor(text)
        muted.setAlpha(190)
        # Group headings in the design's accent colour, so the sections are
        # findable at a glance while scrolling.  Falls back to the palette's
        # highlight when the core theme is not importable (standalone use).
        try:
            from withease.gui import theme as _core_theme
            head = _core_theme.accent()
        except Exception:                            # pragma: no cover
            head = pal.color(pal.ColorRole.Highlight).name()
        return {"alt": alt.name(),
                "rule": rule.name(QColor.NameFormat.HexArgb),
                "muted": muted.name(QColor.NameFormat.HexArgb),
                "head": head}

    def _render(self, needle: str) -> None:
        from html import escape
        needle = (needle or "").strip().casefold()
        c = self._colors()
        rows: list[str] = []
        hits = 0
        for group, items in cde.CHEAT_SHEET:
            # Deliberately NOT matching the group name: searching "Datum" would
            # otherwise return the whole "Zahlen, Datum, Bausteine" group
            # instead of the two commands that actually insert a date.
            found = [(said, means) for said, means in items
                     if not needle
                     or needle in said.casefold()
                     or needle in means.casefold()]
            if not found:
                continue
            rows.append(
                f'<tr><td colspan="2" style="padding:16px 6px 6px 0;'
                f'border-bottom:1px solid {c["rule"]};color:{c["head"]};">'
                f'<b>{escape(group)}</b></td></tr>')
            for i, (said, means) in enumerate(found):
                hits += 1
                bg = f' bgcolor="{c["alt"]}"' if i % 2 else ""
                rows.append(
                    f'<tr><td{bg} style="padding:7px 14px 7px 6px;'
                    f'white-space:nowrap;"><b>{escape(said)}</b></td>'
                    f'<td{bg} style="padding:7px 6px;color:{c["muted"]};">'
                    f'{escape(means)}</td></tr>')
        if not hits:
            self._view.setHtml(
                f'<p style="color:{c["muted"]};">Kein Befehl gefunden. '
                f'Suchfeld leeren, um wieder alle zu sehen.</p>')
            self._count.setText(_t("cheat.count", n="0"))
            return
        # One table for everything, with the command column at a FIXED share of
        # the width: that is what keeps the explanations lined up across all
        # groups instead of jumping with every heading.
        self._view.setHtml(
            '<table width="100%" cellspacing="0" cellpadding="0">'
            '<col width="38%"><col width="62%">' + "".join(rows) + "</table>")
        total = len(cde.cheat_sheet_rows())
        self._count.setText(
            _t("cheat.count.filtered", hits=str(hits), total=str(total))
            if needle else _t("cheat.count", n=str(total)))


class DictationWindow(QWidget):
    """Floating dictation buffer with voice-driven editing and a history."""

    _transcript_sig = Signal(str, str, list)   # (text, mode, low-conf words)
    _state_sig = Signal(str, str)              # (state, mode-label)
    _open_sig = Signal()
    _hide_sig = Signal()                       # hide the window (thread-safe)
    _target_sig = Signal(str)                  # target app name (thread-safe)
    _partial_sig = Signal(str)                 # live provisional text
    _final_sig = Signal(str)                   # live finalised segment
    _polish_sig = Signal(str, bool)            # Whisper-polished sentence, commit
    _ai_actions_sig = Signal(object)           # rebuild the AI action buttons
    _ai_busy_sig = Signal(bool)                # AI request running (disable UI)
    _ai_result_sig = Signal(str)               # replace buffer with AI result
    _ai_msg_sig = Signal(str)                  # short status/error message
    _take_sel_sig = Signal(str)                # selection taken from the target

    def __init__(self, on_insert: Callable[[str], None] | None = None,
                 on_copy: Callable[[str], None] | None = None,
                 on_history_changed: Callable[[list[str]], None] | None = None,
                 on_correction: Callable[[str, str], None] | None = None,
                 on_suggest: Callable[[str], list[str]] | None = None,
                 on_reselect_target: Callable[[], None] | None = None,
                 on_confirm_words: Callable[[list], None] | None = None,
                 on_add_vocab: Callable[[str, str], None] | None = None,
                 on_ai_action: Callable[[str], None] | None = None,
                 on_lookup_snippet: Callable | None = None,
                 on_edit_ai_action: Callable[[int], None] | None = None,
                 on_geometry_changed: Callable[[list], None] | None = None,
                 on_history_toggle: Callable[[bool], None] | None = None,
                 on_ai_toggle: Callable[[bool], None] | None = None,
                 ai_actions: list | None = None,
                 history_visible: bool = False,
                 ai_visible: bool = True,
                 geometry: list | None = None,
                 history: list[str] | None = None,
                 t: Callable[[str], str] | None = None) -> None:
        super().__init__(parent=None)
        self._on_insert = on_insert or (lambda _txt: True)
        self._on_copy = on_copy or (lambda _txt: None)
        self._on_history_changed = on_history_changed or (lambda _items: None)
        self._on_correction = on_correction or (lambda _old, _new: "")
        self._on_suggest = on_suggest or (lambda _wrong: [])
        self._on_reselect_target = on_reselect_target or (lambda: None)
        self._on_confirm_words = on_confirm_words or (lambda _words: None)
        self._on_add_vocab = on_add_vocab or (lambda _s, _w: None)
        self._on_ai_action = on_ai_action or (lambda _prompt: None)
        # Voice-inserted text blocks: the module owns the list, the
        # editor only asks for one by its spoken name.
        self._on_lookup_snippet = (on_lookup_snippet
                                   or (lambda _name: (None, [])))
        self._on_edit_ai_action = on_edit_ai_action or (lambda _i: None)
        self._ai_actions = list(ai_actions or [])   # [(name, prompt), …]
        self._on_geometry_changed = on_geometry_changed or (lambda _g: None)
        self._on_history_toggle = on_history_toggle or (lambda _v: None)
        self._on_ai_toggle = on_ai_toggle or (lambda _v: None)
        self._history_shown = bool(history_visible)
        self._ai_shown = bool(ai_visible)
        self._restore_geometry = geometry
        self._pending_low_words: list[str] = []   # flagged-but-still-here words
        self._tr = t or (lambda s: s)
        self._spell_mode = False
        self._correction_dialog: CorrectionDialog | None = None
        # Live-dictation state: a provisional (grey) region that firms up.
        self._prov_start: int | None = None
        self._prov_end: int | None = None
        self._last_final_end: int | None = None
        self._run_start: int | None = None     # start of the un-polished run
        self._run_text = ""                    # raw text of that run (for guard)

        self.setWindowTitle(_t("win.title"))
        # A normal top-level window (not Qt.Tool): tool windows show a caret but
        # don't take real keyboard activation on Windows, so the user would have
        # to click in first before the arrow keys / typing work.
        self.setWindowFlags(
            Qt.WindowType.Window
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

        # Top bar: KI-Aktionen collapse (left) · status (centred) · Verlauf
        # collapse (right).  Both side panels fold away the same way.
        top = QHBoxLayout()
        self._ai_toggle = QPushButton()
        self._ai_toggle.setToolTip(_wrap_tip(_t("win.ai_toggle")))
        self._ai_toggle.clicked.connect(self._toggle_ai)
        top.addWidget(self._ai_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        self._status = QLabel()
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("font-weight: bold; font-size: larger;")
        top.addWidget(self._status, 0, Qt.AlignmentFlag.AlignCenter)
        top.addStretch(1)
        self._history_btn = QPushButton()
        self._history_btn.setToolTip(_wrap_tip(_t("tip.history")))
        self._history_btn.clicked.connect(self._toggle_history)
        top.addWidget(self._history_btn, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top)

        # --- middle: editor (left) + history (right) in a stable splitter ---
        split = QSplitter(Qt.Orientation.Horizontal)

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(_t("win.placeholder"))
        self._edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._edit.setMinimumWidth(280)
        self._badges = _BadgeOverlay(self._edit)
        # A pill shown centred over the editor while a KI-Aktion runs, so it is
        # obvious that the text is still being worked on.
        self._busy_chip = QLabel("✨  KI arbeitet …", self._edit.viewport())
        self._busy_chip.setStyleSheet(
            "QLabel { background: rgba(20,24,32,0.92); color: #FFFFFF;"
            " border: 1px solid rgba(255,255,255,0.20); border-radius: 16px;"
            " padding: 10px 24px; font-weight: bold; font-size: larger; }")
        self._busy_chip.hide()
        split.addWidget(self._edit)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        hist_label = QLabel(_t("win.history"))
        hist_label.setObjectName("dictHistoryHeader")   # styled via theme QSS
        right_layout.addWidget(hist_label)
        self._history = QListWidget()
        self._history.setObjectName("dictHistory")   # separators via theme QSS
        self._history.setAlternatingRowColors(True)  # clear entry distinction
        self._history.setWordWrap(True)
        self._history.itemClicked.connect(self._load_history)
        right_layout.addWidget(self._history, 1)
        # Past dictations are kept in the profile file, in plain text – so
        # there has to be a way to get rid of them.  Before this there was
        # none: "Leeren" archives the buffer INTO the history.
        self._hist_clear = QPushButton(_t("win.history.clear"))
        _mark_danger(self._hist_clear)
        self._hist_clear.setToolTip(_wrap_tip(
            _t("tip.history.clear")))
        self._hist_clear.clicked.connect(self._clear_history)
        right_layout.addWidget(self._hist_clear)
        right.setMinimumWidth(150)
        right.setMaximumWidth(280)
        split.addWidget(right)
        self._history_panel = right
        right.setVisible(self._history_shown)   # default collapsed, remembered

        split.setStretchFactor(0, 1)    # editor grows
        split.setStretchFactor(1, 0)    # history keeps its width
        split.setSizes([560, 200])

        # Left column: user-configurable _t("win.ai_column") – each button applies its
        # own prompt to the buffer via the configured LLM (e.g. turn the text
        # into an email or bullet points).
        mid = QHBoxLayout()
        mid.setSpacing(8)
        self._ai_bar = QVBoxLayout()
        self._ai_bar.setSpacing(4)
        self._ai_widget = QWidget()
        self._ai_widget.setLayout(self._ai_bar)
        self._ai_widget.setFixedWidth(140)      # updated once actions load
        self._ai_buttons: list = []
        mid.addWidget(self._ai_widget)
        mid.addWidget(split, 1)
        layout.addLayout(mid, 1)
        self._rebuild_ai_bar()
        self._update_history_btn()

        # Which app "einfügen" will paste into.
        self._target_label = QLabel("")
        layout.addWidget(self._target_label)

        # Small toolbar: undo/redo, re-pick target app, command help.
        tools = QHBoxLayout()
        undo_btn = QPushButton(_t("win.undo"))
        undo_btn.clicked.connect(lambda: self._editor.te.undo())
        tools.addWidget(undo_btn)
        redo_btn = QPushButton(_t("win.redo"))
        redo_btn.clicked.connect(lambda: self._editor.te.redo())
        tools.addWidget(redo_btn)
        # A broom, not a bin: this does NOT delete anything – the text is
        # archived into the history first and only then the buffer is emptied.
        # The bin icon promised a loss that never happens.
        clear_btn = QPushButton(_t("win.clear"))
        clear_btn.setToolTip(_wrap_tip(_t("tip.clear")))
        clear_btn.clicked.connect(self._do_clear)
        tools.addWidget(clear_btn)
        vocab_btn = QPushButton(_t("win.vocab"))
        vocab_btn.setToolTip(_wrap_tip(_t("tip.vocab")))
        vocab_btn.clicked.connect(self._add_selection_to_vocab)
        tools.addWidget(vocab_btn)
        self._reselect_btn = QPushButton(_t("win.target"))
        self._reselect_btn.clicked.connect(lambda: self._on_reselect_target())
        tools.addWidget(self._reselect_btn)
        help_btn = QPushButton(_t("win.commands"))
        help_btn.clicked.connect(self._show_cheatsheet)
        tools.addWidget(help_btn)
        tools.addStretch()
        self._counter = QLabel("")               # live char/word count
        tools.addWidget(self._counter)
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
        self._style_hint()
        layout.addWidget(self._hint)

        # --- buttons (each with a keyboard shortcut) ---
        row = QHBoxLayout()
        # Labels stay short so the buttons don't get clipped at large font
        # sizes; the keyboard shortcut lives in the tooltip (hover to see it)
        # while setShortcut keeps the key binding active.
        self._insert_btn = QPushButton(_t("win.insert_close"))
        self._insert_btn.setShortcut("Ctrl+Return")
        self._insert_btn.setToolTip(_wrap_tip(_t("tip.insert_close")))
        self._insert_btn.clicked.connect(self._do_insert)
        row.addWidget(self._insert_btn)
        self._insert_keep_btn = QPushButton(_t("win.insert_keep"))
        self._insert_keep_btn.setToolTip(_wrap_tip(
            _t("tip.insert_keep")))
        self._insert_keep_btn.clicked.connect(
            lambda: self._do_insert(keep_open=True))
        row.addWidget(self._insert_keep_btn)

        self._copy_btn = QPushButton(_t("win.copy"))
        self._copy_btn.setShortcut("Ctrl+Shift+C")
        self._copy_btn.setToolTip(_wrap_tip("In die Zwischenablage kopieren "
                                  "(Strg+Umschalt+C)"))
        self._copy_btn.clicked.connect(self._do_copy)
        row.addWidget(self._copy_btn)
        self._copy_close_btn = QPushButton(_t("win.copy_close"))
        self._copy_close_btn.setShortcut("Ctrl+Shift+Return")
        self._copy_close_btn.setToolTip(_wrap_tip(_t("tip.copy_close")))
        self._copy_close_btn.clicked.connect(self._do_copy_and_close)
        row.addWidget(self._copy_close_btn)
        self._close_btn = QPushButton(_t("win.close"))
        self._close_btn.setShortcut("Ctrl+W")
        self._close_btn.setToolTip(_wrap_tip(_t("tip.close")))
        self._close_btn.clicked.connect(self._close_and_clear)
        row.addWidget(self._close_btn)
        row.addStretch()
        layout.addLayout(row)

        self._editor = ea.Editor(self._edit)
        self._editor.snippet_lookup = self._on_lookup_snippet
        self._rec_timer = QTimer(self)          # recording elapsed-time ticker
        self._rec_timer.setInterval(1000)
        self._rec_timer.timeout.connect(self._tick_recording)
        self._rec_secs = 0
        self._status_base = ""
        self._edit.textChanged.connect(self._update_counter)
        self._update_counter()
        self._edit.installEventFilter(self)      # Escape cancels „nimm N“
        self._load_initial_history(history or [])
        self._transcript_sig.connect(self._on_transcript)
        self._state_sig.connect(self._apply_state)
        self._open_sig.connect(self.open_for_dictation)
        self._hide_sig.connect(self.hide)
        self._target_sig.connect(self._apply_target)
        self._ai_actions_sig.connect(self._apply_ai_actions)
        self._ai_busy_sig.connect(self._apply_ai_busy)
        self._ai_result_sig.connect(self._apply_ai_result)
        self._ai_msg_sig.connect(self._apply_ai_message)
        self._take_sel_sig.connect(self._apply_take_selected)
        self._partial_sig.connect(self._apply_partial)
        self._final_sig.connect(self._apply_final)
        self._polish_sig.connect(self._apply_polish)
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

    # -- KI-Aktionen (thread-safe) -------------------------------------

    def set_ai_actions(self, actions: list) -> None:
        self._ai_actions_sig.emit(list(actions or []))

    def ai_busy(self, on: bool = True) -> None:
        self._ai_busy_sig.emit(bool(on))

    def ai_result(self, text: str) -> None:
        self._ai_result_sig.emit(text or "")

    def ai_message(self, msg: str) -> None:
        self._ai_msg_sig.emit(msg or "")

    def _rebuild_ai_bar(self) -> None:
        while self._ai_bar.count():
            item = self._ai_bar.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._ai_buttons = []
        if self._ai_actions:
            header = QLabel(_t("win.ai_column"))
            header.setStyleSheet("font-weight: bold;")
            self._ai_bar.addWidget(header)
            for idx, (name, prompt) in enumerate(self._ai_actions):
                btn = QPushButton(name or "…")
                btn.setToolTip(_wrap_tip((prompt[:300] + "\n\n") + "Rechtsklick: bearbeiten"))
                btn.clicked.connect(
                    lambda _=False, p=prompt: self._on_ai_action(p))
                btn.setContextMenuPolicy(
                    Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, i=idx, b=btn: self._ai_button_menu(i, b, pos))
                self._ai_bar.addWidget(btn)
                self._ai_buttons.append(btn)
            self._ai_bar.addStretch()
            # Widen the column to fit the longest button label (so names like
            # "Sauber formulieren" or a larger font size don't get clipped).
            # Use each button's own sizeHint – it already accounts for the
            # button's QSS padding/border – plus the column layout's own
            # margins, capped so it can't take over the whole window.
            margins = self._ai_bar.contentsMargins()
            needed = max((b.sizeHint().width() for b in self._ai_buttons),
                        default=0) + margins.left() + margins.right()
            self._ai_widget.setFixedWidth(max(140, min(240, needed)))
        self._update_ai_panel()

    def _toggle_ai(self) -> None:
        self._ai_shown = not self._ai_shown
        self._update_ai_panel()
        self._on_ai_toggle(self._ai_shown)

    def _update_ai_panel(self) -> None:
        """Show the KI-Aktionen column only when it holds actions AND the user
        hasn't folded it away; the top-left toggle appears only when there is
        something to fold."""
        has = bool(self._ai_actions)
        self._ai_toggle.setVisible(has)
        self._ai_widget.setVisible(has and self._ai_shown)
        self._ai_toggle.setText(_t("btn.ai.open") if self._ai_shown
                                else _t("btn.ai.closed"))

    def _ai_button_menu(self, index: int, button, pos) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(button)
        edit = menu.addAction("Bearbeiten …")
        edit.triggered.connect(lambda: self._on_edit_ai_action(index))
        menu.exec(button.mapToGlobal(pos))

    def _apply_ai_actions(self, actions: list) -> None:
        self._ai_actions = list(actions or [])
        self._rebuild_ai_bar()

    def _position_busy_chip(self) -> None:
        self._busy_chip.adjustSize()
        r = self._edit.viewport().rect()
        self._busy_chip.move(
            max(0, (r.width() - self._busy_chip.width()) // 2),
            max(0, (r.height() - self._busy_chip.height()) // 2))

    def _apply_ai_busy(self, on: bool) -> None:
        for b in self._ai_buttons:
            b.setEnabled(not on)
        if on:
            self._hint.setText("KI arbeitet …")
            self._position_busy_chip()
            self._busy_chip.show()
            self._busy_chip.raise_()
        else:
            self._busy_chip.hide()

    def _apply_ai_message(self, msg: str) -> None:
        self._busy_chip.hide()
        self._hint.setText(msg)

    def _apply_ai_result(self, text: str) -> None:
        self._busy_chip.hide()
        for b in self._ai_buttons:
            b.setEnabled(True)
        original = self._edit.toPlainText()
        if original.strip() == text.strip():
            self._hint.setText(_t("msg.ai_no_change"))
            return
        # Preview with the changes highlighted → compare, then decide.  Modal
        # but non-blocking (show(), not exec()) so nothing freezes.
        dlg = _AiPreview(original, text, parent=self)
        dlg.setModal(True)
        dlg.accepted.connect(lambda t=text: self._ai_apply_accepted(t))
        dlg.rejected.connect(lambda: self._set_hint("KI-Ergebnis verworfen"))
        self._ai_preview = dlg          # keep a ref so it isn't GC'd
        dlg.show()

    def _ai_apply_accepted(self, text: str) -> None:
        cur = self._edit.textCursor()
        cur.beginEditBlock()
        cur.select(QTextCursor.SelectionType.Document)
        cur.insertText(text)
        cur.endEditBlock()
        self._edit.setTextCursor(cur)
        self._set_hint(_t("msg.ai_kept"))

    # -- live dictation (thread-safe) ----------------------------------

    def live_partial(self, text: str) -> None:
        self._partial_sig.emit(text or "")

    def live_final(self, text: str) -> None:
        self._final_sig.emit(text or "")

    def live_polish(self, text: str, commit: bool = True) -> None:
        self._polish_sig.emit(text or "", bool(commit))

    def _prov_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(150, 150, 150))   # grey = provisional
        return fmt

    def _begin_run_at_cursor(self, text: str,
                             fmt: QTextCharFormat) -> tuple[int, int]:
        """Start a new live utterance at the *current cursor* (not the end), so
        „Cursor vor X" and manual clicks let you dictate into the middle of the
        text.  Adds a leading space if it would glue to the previous word and a
        trailing space if it would glue to a following word.  Returns the
        (start, end) of the inserted text (trailing space excluded) and leaves
        the edit cursor at `end`."""
        cur = self._edit.textCursor()
        cur.clearSelection()
        doc = self.text()
        pos = cur.position()
        # Whisper capitalises every utterance like a sentence of its own, so a
        # continuation used to read "…und dann Das war gut."  join_dictation
        # decides both the separating space AND the capitalisation from what
        # stands immediately before the cursor.
        from postprocess import join_dictation
        text = join_dictation(doc[:pos], text, doc[pos:])
        start_offset = len(text) - len(text.lstrip(" "))
        if start_offset:
            cur.insertText(text[:start_offset])
            text = text[start_offset:]
        start = cur.position()
        cur.insertText(text, fmt)
        end = cur.position()
        doc = self.text()
        if end < len(doc) and doc[end].isalnum():
            cur.insertText(" ")                # separate from the following word
            cur.setPosition(end)               # keep cursor at end of new text
        self._edit.setTextCursor(cur)
        return start, end

    def _apply_partial(self, text: str) -> None:
        text = " ".join((text or "").split())
        if self._prov_start is None:
            self._prov_start, self._prov_end = self._begin_run_at_cursor(
                text, self._prov_format())
        else:
            cur = self._edit.textCursor()
            cur.setPosition(self._prov_start)
            cur.setPosition(self._prov_end, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(text, self._prov_format())
            self._prov_end = cur.position()
            self._edit.setTextCursor(cur)

    def _apply_final(self, text: str) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        plain = QTextCharFormat()
        if self._prov_start is not None:
            cur = self._edit.textCursor()
            cur.setPosition(self._prov_start)
            cur.setPosition(self._prov_end, QTextCursor.MoveMode.KeepAnchor)
            start = self._prov_start
            cur.insertText(text, plain)
            end = cur.position()
            self._edit.setTextCursor(cur)
            inserted = text
        else:
            start, end = self._begin_run_at_cursor(text, plain)
            # What LANDED in the document, not what was passed in: joining may
            # have changed the first letter's case.  Remembering the raw text
            # made the polish step's "is this run still untouched?" check fail,
            # so the polished sentence was dropped – visible as a missing full
            # stop at the end of a sentence.
            inserted = self.text()[start:end]
        self._last_final_end = end
        if self._run_start is None:
            self._run_start = start
            self._run_text = inserted
        else:
            self._run_text += " " + inserted    # fragments joined by one space
        self._prov_start = self._prov_end = None

    def _apply_polish(self, text: str, commit: bool = True) -> None:
        """Replace the current sentence's run with Whisper's polished text.

        With ``commit=False`` the sentence isn't finished yet (Whisper's text
        doesn't end on „.", „!", „?"): the run stays *open* so the next words
        extend it and the next polish replaces the whole growing sentence – that
        is what gives proper sentence-wide punctuation, casing and compound
        words instead of one capitalised fragment per pause."""
        text = " ".join((text or "").split())
        if not text:
            if commit:
                self._run_start, self._run_text = None, ""
                self._last_final_end = None
            return
        if self._run_start is None or self._last_final_end is None:
            # No Vosk run to replace (Whisper-only live mode): insert the polished
            # text as a fresh run at the cursor, and keep it open until committed.
            start, end = self._begin_run_at_cursor(text, QTextCharFormat())
            if commit:
                self._run_start, self._run_text = None, ""
                self._last_final_end = None
            else:
                # Same reason as in _apply_final: track what really landed in
                # the document, not what was passed in.
                self._run_start, self._run_text, self._last_final_end = \
                    start, self.text()[start:end], end
            return
        start, end = self._run_start, self._last_final_end
        cur = self._edit.textCursor()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        if cur.selectedText() == self._run_text:
            cur.insertText(text, QTextCharFormat())
            new_end = cur.position()
            self._shift_offsets(end, new_end - end)      # keep tracking sane
            if self._prov_start is None:      # no next word streaming yet →
                self._edit.setTextCursor(cur)  # keep dictating after this text
            if not commit:
                # sentence continues: keep the run open on the polished text
                self._run_start = start
                self._run_text = text
                self._last_final_end = new_end
                return
        self._run_start, self._run_text = None, ""
        self._last_final_end = None

    def _shift_offsets(self, after: int, delta: int) -> None:
        """A polish replaced text of a different length; move any offsets that
        point past it (e.g. a provisional region of the next utterance)."""
        if not delta:
            return
        for attr in ("_prov_start", "_prov_end", "_last_final_end"):
            val = getattr(self, attr)
            if val is not None and val >= after:
                setattr(self, attr, val + delta)

    def _style_hint(self) -> None:
        self._hint.setStyleSheet(
            "QLabel { color: palette(text); background: palette(base);"
            " border: 1px solid palette(mid); border-radius: 4px;"
            " padding: 3px 8px; }")

    def reapply_theme(self) -> None:
        """Re-resolve palette()-based stylesheets after a live theme switch.

        Qt bakes the ``palette(...)`` colours of a stylesheet *string* at the
        moment it is set, so widgets styled that way keep their old colours when
        the app palette flips light<->dark while the window is already open.
        Re-setting the string resolves it against the current palette again."""
        self._style_hint()

    def request_open(self) -> None:
        """Show the window (safe to call from a worker thread)."""
        self._open_sig.emit()

    def take_selected_text(self, text: str) -> None:
        """Start from the text that was selected in the target app (thread-safe).

        Only ever fills an EMPTY buffer: a dictation already in progress must
        never be overwritten by a stray selection somewhere else."""
        self._take_sel_sig.emit(text or "")

    def _apply_take_selected(self, text: str) -> None:
        text = (text or "").strip()
        if not text or self.text().strip():
            return
        self._edit.setPlainText(text)
        cur = self._edit.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self._edit.setTextCursor(cur)
        self._set_hint(_t("msg.took_selection"))

    def request_hide(self) -> None:
        """Hide the window (safe to call from a worker thread).  Used while the
        user picks a new target app, so the dictation window is out of the way
        and can't be captured as the target."""
        self._hide_sig.emit()

    def is_correcting(self) -> bool:
        """True while the correction sub-window is open, so the module keeps the
        existing paste target instead of capturing the correction window."""
        dlg = self._correction_dialog
        return dlg is not None and dlg.isVisible()

    def text(self) -> str:
        return self._edit.toPlainText()

    def open_for_dictation(self) -> None:
        was_hidden = not self.isVisible()
        self.reapply_theme()          # in case the theme changed while hidden
        if was_hidden:
            self.show()
        self.raise_()
        # If the correction window is open, keep the focus there (so a spoken
        # correction lands in its field and it stays editable) instead of
        # pulling focus back into the main edit.
        dlg = self._correction_dialog
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            dlg._field.setFocus()
            return
        self.activateWindow()
        hwnd = int(self.winId())
        _win_force_foreground(hwnd)                  # beat the foreground lock
        # Only on the first appearance: put the caret at the end so there is a
        # visible caret.  While the window stays open, leave the cursor where it
        # is (and keep any selection) so dictation inserts at that position.
        cur = self._edit.textCursor()
        if was_hidden and not cur.hasSelection():
            cur.movePosition(QTextCursor.MoveOperation.End)
            self._edit.setTextCursor(cur)
        self._edit.setFocus()
        # Re-assert once the window is fully shown (activation can be deferred).
        QTimer.singleShot(0, lambda: (_win_force_foreground(hwnd),
                                      self._edit.setFocus()))

    # -- main-thread slots ---------------------------------------------

    def _apply_state(self, state: str, mode: str = "") -> None:
        key, colour = _STATE.get(state, _STATE["idle"])
        label = _t(key)         # translated HERE, not in the table at import
        if mode and state in ("recording", "transcribing"):
            label = f"{label}   ·   {mode}"
        # Keep the font size identical across every state so the label's height
        # never changes and the window doesn't shift up/down when recording
        # starts/stops.  Recording still stands out via the red colour and the
        # running timer – not a bigger font.
        if state == "recording":
            self._rec_secs = 0
            self._status_base = label
            self._status.setText(f"{label}    0 s")
            colour = "#e53935"
            self._rec_timer.start()
        else:
            self._rec_timer.stop()
            self._status.setText(label)
        self._status.setStyleSheet(
            f"font-weight: bold; font-size: larger; color: {colour};")

    def _tick_recording(self) -> None:
        self._rec_secs += 1
        self._status.setText(f"{self._status_base}    {self._rec_secs} s")

    def _update_counter(self) -> None:
        text = self._edit.toPlainText()
        words = len(text.split())
        self._counter.setText(f"{len(text)} · {words} " + _t("win.words"))

    def _do_clear(self) -> None:
        """Leeren-Button: keep the text (archive to history), then clear the
        buffer undoably (Strg+Z restores it)."""
        if self._edit.toPlainText().strip():
            self._archive()
        cur = self._edit.textCursor()
        cur.beginEditBlock()
        cur.select(QTextCursor.SelectionType.Document)
        cur.removeSelectedText()
        cur.endEditBlock()
        self._editor = ea.Editor(self._edit)
        self._editor.snippet_lookup = self._on_lookup_snippet
        self._clear_marks()
        self._set_hint(_t("msg.cleared"))

    def _on_transcript(self, text: str, mode: str = "auto",
                       low_words: list | None = None) -> None:
        # Correction window open: route speech into it (fill field / confirm).
        if self._correction_dialog is not None:
            if self._correction_dialog.isVisible():
                self._correction_dialog.handle_voice(text)
                self._report(text, _t("msg.to_correction"))
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
                self._report(text, _t("msg.nothing"))
            return

        # Dictation key (or explicit text mode): never interpret as a command.
        if mode == "text":
            self._editor.insert_dictation(cde.apply_inline_punctuation(text))
            self._forward_correction()
            self._highlight_low_words(low_words)
            self._report(text, _t("msg.as_text"))
            return

        cmd = cde.parse(text)
        if cmd is None:
            if mode == "command":
                # Command key but nothing matched: do not dump text into buffer.
                self._report(text, _t("msg.no_command"))
                return
            self._editor.insert_dictation(cde.apply_inline_punctuation(text))
            self._forward_correction()
            self._highlight_low_words(low_words)
            self._report(text, _t("msg.as_text"))
            return

        # Window-level commands handled here; editing commands go to the editor.
        if cmd.kind == "insert":
            # sets its own hint (success closes or stays open, else fallback)
            self._do_insert(keep_open=bool(cmd.data.get("keep_open")))
            return
        if cmd.kind == "copy":
            self._do_copy()
            self._report(text, _t("msg.copied"))
            return
        if cmd.kind == "close":
            self._report(text, _t("msg.closing"))
            self._close_and_clear()
            return
        if cmd.kind == "show_help":
            self._show_cheatsheet()
            self._report(text, _t("msg.cheatsheet"))
            return
        if cmd.kind == "history_show":
            if not self._history_shown:
                self._toggle_history()
            n = self._history.count()
            self._report(text, _t("msg.history_count", n=str(n),
                                  max=str(min(n, 9)))
                         if n else _t("msg.history_empty"))
            return
        if cmd.kind == "history_pick":
            self._insert_from_history(int(cmd.data.get("n", 1)), text)
            return
        if cmd.kind == "reselect_target":
            self._on_reselect_target()
            self._report(text, _t("msg.retarget"))
            return
        if cmd.kind == "spell_mode":
            self._spell_mode = True
            self._report(text, _t("msg.spell_mode"))
            return
        if cmd.kind == "spell_inline":
            word = cde.spell_to_text(cmd.data.get("text", ""))
            if word:
                self._editor.insert_dictation(word)
                self._forward_correction()
                self._report(text, f"buchstabiert → {word}")
            else:
                self._report(text, _t("msg.nothing"))
            return

        res = self._editor.apply(cmd)
        self._forward_correction()      # "ersetze A durch B" learns here too
        if res.status == "awaiting_dictation":
            # "korrigiere …" selected the target → open the correction window.
            self._clear_marks()
            self._open_correction(self._edit.textCursor().selectedText())
            self._report(text, _t("msg.correction_open"))
        elif res.status == "ambiguous" and res.matches:
            legend = self._mark_candidates(res.matches)
            self._report(text, legend)
        else:
            self._clear_marks()
            self._report(text, res.message or f"Befehl: {cmd.kind}")

    def _do_insert(self, keep_open: bool = False) -> None:
        text = self.text().strip()
        if not text:
            return
        ok = self._on_insert(text)
        if ok is False:
            # Target app gone/invalid: text was put on the clipboard instead.
            self._set_hint(_t("msg.paste_failed"))
            return
        self._confirm_low_words()   # accepted unchanged → learn they were right
        if keep_open:
            # "Einfügen und weiter": hand the text over but stay open, so a
            # long text can be dictated paragraph by paragraph without
            # reopening the window and picking the target app again.
            self._archive_and_clear()
            self._set_hint(_t("msg.pasted"))
            return
        # Plain "Einfügen" hands the text over and gets out of the way: the
        # window archives + clears + closes in the same moment, so the next
        # dictation starts fresh without an extra "schließen".
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
            self._set_hint(_t("msg.clipboard"))

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
        item.setToolTip(_wrap_tip(text))
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
        item.setToolTip(_wrap_tip(text))
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
        self._prov_start = self._prov_end = None
        self._last_final_end = None
        self._run_start = None
        self._run_text = ""
        # Fresh editor state (drops any pending selection / correction).
        self._editor = ea.Editor(self._edit)
        self._editor.snippet_lookup = self._on_lookup_snippet

    def _archive_and_clear(self) -> None:
        """Put the finished text into the history and start an empty buffer –
        the same bookkeeping as closing, minus the closing."""
        self._archive()
        self._clear_buffer()

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
        if name:
            self._target_label.setText(_t("win.target_is", app=name))
            self._target_label.setStyleSheet("")     # inherit the theme palette
        else:
            self._target_label.setText(_t("win.no_target"))
            self._target_label.setStyleSheet(
                "color: #e0812b; font-weight: bold;")

    def set_reselecting(self, on: bool) -> None:
        """Highlight the target-app button while the user is picking an app."""
        if on:
            self._reselect_btn.setText("🎯 … zur App wechseln + Leertaste (Esc)")
            self._reselect_btn.setStyleSheet(
                "QPushButton { background: #e0812b; color: white;"
                " font-weight: bold; }")
        else:
            self._reselect_btn.setText(_t("win.target"))
            self._reselect_btn.setStyleSheet("")
            # Setting a QSS with font properties (font-weight above) bakes the
            # current font size onto the button and breaks its inheritance of
            # the app font – so it stayed enlarged after the global font size
            # was changed back.  A default QFont() clears that override so the
            # button follows the application font again.
            from PySide6.QtGui import QFont
            self._reselect_btn.setFont(QFont())

    def _update_history_btn(self) -> None:
        self._history_btn.setText(_t("btn.history.open") if self._history_shown
                                  else _t("btn.history.closed"))

    def _insert_from_history(self, n: int, said: str) -> None:
        """Put past dictation number `n` back into the buffer.

        The history held twenty finished dictations but could only be reached
        with the mouse – after an accidental „Einfügen" there was no spoken way
        back to the text."""
        count = self._history.count()
        if count == 0:
            self._report(said, _t("msg.history_empty"))
            return
        if not 1 <= n <= count:
            self._report(said, _t("msg.history_only", n=str(count)))
            return
        item = self._history.item(n - 1)
        past = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if not past:
            self._report(said, _t("msg.entry_empty"))
            return
        self._editor.insert_dictation(past)
        self._report(said, _t("msg.history_inserted", n=str(n)))

    def _clear_history(self) -> None:
        """Forget every stored dictation – and offer to take it back.

        Used to ask first, which cost a second precise click right after the
        one that may already have been a slip.  Now the list goes and the way
        back is one big button (see widgets/undo_bar.py)."""
        n = self._history.count()
        if n == 0:
            self._set_hint(_t("msg.history_already_empty"))
            return
        removed = [self._history.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(n)]

        def undo() -> None:
            self.reload_history(removed)
            self._on_history_changed(list(removed))
            self._set_hint(_t("msg.history_restored"))

        if not _show_undo(self, _t("msg.deleted_count", n=str(n)), undo):
            answer = QMessageBox.question(
                self, _t("confirm.history.title"),
                _t("confirm.history.text", n=str(n)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._history.clear()
        self._on_history_changed([])
        self._set_hint(_t("msg.history_deleted"))

    def reload_history(self, entries: list) -> None:
        """Replace the list with ``entries`` (newest first) – used by undo and
        by the settings page when it puts a cleared history back."""
        self._history.clear()
        for text in entries:
            if not text:
                continue
            label = " ".join(str(text).split())
            if len(label) > _HISTORY_LABEL_LEN:
                label = label[:_HISTORY_LABEL_LEN - 1] + "…"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setToolTip(_wrap_tip(str(text)))
            self._history.addItem(item)

    def clear_history_now(self) -> None:
        """Wipe the history list without asking (the settings page already
        asked).  Safe from the GUI thread only."""
        self._history.clear()

    def _toggle_history(self) -> None:
        self._history_shown = not self._history_shown
        self._history_panel.setVisible(self._history_shown)
        self._update_history_btn()
        self._on_history_toggle(self._history_shown)

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

    def _add_selection_to_vocab(self) -> None:
        """Add the selected word to the spoken→written dictionary, asking how it
        is pronounced."""
        written = self._edit.textCursor().selectedText().strip()
        if not written:
            self._set_hint(_t("msg.mark_word_first"))
            return
        spoken, ok = QInputDialog.getText(
            self, _t("vocab.add"),
            _t("vocab.ask", word=written), text=written.lower())
        if ok and spoken.strip():
            self._on_add_vocab(spoken.strip(), written)
            self._set_hint(_t("vocab.added", spoken=spoken.strip(),
                              written=written))

    def _load_history(self, item: QListWidgetItem) -> None:
        text = item.data(Qt.ItemDataRole.UserRole)
        if not text:
            return
        # Just load the clicked entry's text into the buffer – do NOT archive
        # the current buffer here.  Archiving on every click re-added the (often
        # previously-loaded) buffer to the top, which created duplicates and
        # reordered the list.  The replace is undoable (select-all + insert), so
        # Strg+Z still brings the previous text back; the buffer is archived
        # normally on "Einfügen"/"Kopieren & Schließen".
        cur = self._edit.textCursor()
        cur.beginEditBlock()
        cur.select(QTextCursor.SelectionType.Document)
        cur.insertText(text)
        cur.endEditBlock()
        self._editor = ea.Editor(self._edit)
        self._editor.snippet_lookup = self._on_lookup_snippet
        self._clear_marks()
        cur = self._edit.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self._edit.setTextCursor(cur)
        self._set_hint(_t("msg.history_loaded"))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # The window is reusable: hide + clear instead of destroying it.
        event.ignore()
        self._close_and_clear()

    def _forward_correction(self) -> None:
        """If the last edit replaced a word, let the error memory learn it –
        and SAY so.

        The memory needs the same correction twice before it applies it (a
        deliberate guard against one stray edit poisoning a common word).  That
        rule was invisible: correcting a word looked like it had no effect at
        all, so people corrected the same word again and again without knowing
        they were on the right track."""
        pair = self._editor.last_correction
        if not pair:
            return
        self._editor.last_correction = None
        stage = self._on_correction(pair[0], pair[1])
        if stage == "always":
            self._set_hint(f"„{pair[0]}“ → „{pair[1]}“ zum zweiten Mal "
                           f"korrigiert – wird ab jetzt immer angewendet")
        elif stage == "uncertain":
            self._set_hint(f"„{pair[0]}“ → „{pair[1]}“ gemerkt – wird "
                           f"angewendet, wenn die Erkennung unsicher ist")

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
        dlg.focus_field()
        # Re-assert focus after the event loop settles the new window.
        QTimer.singleShot(0, dlg.focus_field)

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
            self._set_hint(_t("msg.correction_cancelled"))

    def _cancel_correction(self) -> None:
        self._correction_dialog = None
        self._set_hint(_t("msg.correction_cancelled"))

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
        self._hint.setText(_t("msg.recognised", raw=raw, outcome=outcome))

    # -- numbered "nimm N" candidates ----------------------------------

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        # Escape while the numbered „nimm N“ choices are showing → cancel them
        # (instead of picking one).
        if (obj is self._edit and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
                and self._editor.has_pending()):
            self._cancel_candidates()
            return True
        # Keep the „KI arbeitet …“ pill centred when the editor is resized.
        if (obj is self._edit and event.type() == QEvent.Type.Resize
                and self._busy_chip.isVisible()):
            self._position_busy_chip()
        return super().eventFilter(obj, event)

    def _cancel_candidates(self) -> None:
        self._editor.cancel_pending()
        self._clear_marks()
        self._set_hint(_t("msg.selection_cancelled"))

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
        return _t("msg.matches", n=str(len(matches)),
                  list="   ".join(parts))

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
