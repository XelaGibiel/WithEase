"""Applies parsed voice :class:`Command`s to a Qt text buffer.

The dictation window owns a ``QPlainTextEdit``; this module turns a
:class:`commands_de.Command` (or a piece of dictation text) into concrete
edits on that buffer's ``QTextCursor``.  Everything here is deterministic and
unit-testable on an offscreen ``QPlainTextEdit`` – no audio involved.

Word targeting is fuzzy (case/accent tolerant + small edit distance) so a
slightly mis-heard word still finds its match.  When a target occurs several
times the action is deferred: the matches are reported and ``pick(n)`` resolves
which one (spoken as "nimm zwei").
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from PySide6.QtGui import QTextCursor


@dataclass
class ActionResult:
    status: str = "ok"       # ok | ambiguous | not_found | awaiting_dictation | info
    count: int = 0
    matches: list = field(default_factory=list)   # [(start, end), …] for ambiguous
    message: str = ""


def _fold(text: str) -> str:
    """Lower-case + strip diacritics for tolerant comparison (ä→a, ß→ss)."""
    text = text.lower().replace("ß", "ss")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_eq(ft: str, fw: str) -> bool:
    """True if two *already folded* words match (exact / prefix / small edit)."""
    if not ft or not fw:
        return False
    if fw == ft or fw.startswith(ft) or ft.startswith(fw):
        return True
    tol = 1 if len(ft) <= 5 else 2
    return _levenshtein(fw, ft) <= tol


def _word_matches(target: str, tokens: list[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """Fuzzy-match a single ``target`` word against document tokens; return
    positions in document order (exact/prefix first, then close edit-distance)."""
    ft = _fold(target)
    if not ft:
        return []
    exact, near = [], []
    tol = 1 if len(ft) <= 5 else 2
    for start, end, word in tokens:
        fw = _fold(word)
        if fw == ft or fw.startswith(ft) or ft.startswith(fw):
            exact.append((start, end))
        elif _levenshtein(fw, ft) <= tol:
            near.append((start, end))
    return exact or near


def _target_spans(target: str, tokens: list[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """Fuzzy-match a target that may be a single word *or* a multi-word phrase.

    A phrase matches where consecutive document tokens each fuzzy-match the
    corresponding target word, so „Cursor vor <mehrere Wörter>“ works too."""
    parts = [p for p in re.split(r"\s+", target.strip()) if p]
    if not parts:
        return []
    if len(parts) == 1:
        return _word_matches(parts[0], tokens)
    folded = [_fold(p) for p in parts]
    n = len(folded)
    spans: list[tuple[int, int]] = []
    for i in range(len(tokens) - n + 1):
        if all(_fuzzy_eq(folded[j], _fold(tokens[i + j][2])) for j in range(n)):
            spans.append((tokens[i][0], tokens[i + n - 1][1]))
    return spans


class Editor:
    """Wraps a QPlainTextEdit and applies commands / dictation to it."""

    def __init__(self, text_edit) -> None:
        self.te = text_edit
        self._pending: dict | None = None      # deferred multi-match op
        self._last_insert: tuple[int, int] | None = None
        self._awaiting_correction = False

    # -- helpers --------------------------------------------------------

    def _text(self) -> str:
        return self.te.toPlainText()

    def _tokens(self) -> list[tuple[int, int, str]]:
        return [(m.start(), m.end(), m.group())
                for m in re.finditer(r"\w+", self._text(), re.UNICODE)]

    def _set_cursor(self, pos: int, anchor: int | None = None) -> None:
        cur = self.te.textCursor()
        if anchor is None:
            cur.setPosition(pos)
        else:
            cur.setPosition(anchor)
            cur.setPosition(pos, QTextCursor.MoveMode.KeepAnchor)
        self.te.setTextCursor(cur)

    def _selection(self) -> tuple[int, int]:
        cur = self.te.textCursor()
        return cur.selectionStart(), cur.selectionEnd()

    # -- dictation text -------------------------------------------------

    def insert_dictation(self, text: str) -> ActionResult:
        """Insert recognised dictation text.  Replaces the current selection
        (this is how 'markiere X' → say replacement and 'korrigiere X' work)."""
        text = text.strip()
        if not text:
            return ActionResult("info", message="leer")
        cur = self.te.textCursor()
        self._awaiting_correction = False
        if cur.hasSelection():
            # Replacing a selection = a correction: strip the sentence
            # punctuation Whisper tends to append to a single spoken word, so
            # "Testen." doesn't drop a stray period into the middle of a line.
            text = text.rstrip(" .,;:!?…") or text
        else:
            # Smart spacing: add a leading space between words.
            prev = self._text()[:cur.position()]
            if prev and prev[-1].isalnum() and (text[0].isalnum() or text[0] in "„("):
                text = " " + text
        start = cur.selectionStart()
        cur.insertText(text)
        self._last_insert = (start, start + len(text))
        self.te.setTextCursor(cur)
        return ActionResult("ok")

    # -- command dispatch ----------------------------------------------

    def apply(self, cmd) -> ActionResult:
        handler = getattr(self, f"_do_{cmd.kind}", None)
        if handler is None:
            return ActionResult("info", message=f"unbekannt: {cmd.kind}")
        return handler(cmd.data)

    # -- pick (resolve an ambiguous target) ----------------------------

    def _do_pick(self, data: dict) -> ActionResult:
        if not self._pending:
            return ActionResult("info", message="nichts auszuwählen")
        n = int(data.get("n", 0))
        matches = self._pending["matches"]
        if not (1 <= n <= len(matches)):
            return ActionResult("info", message="ungültige Nummer")
        op, extra = self._pending["op"], self._pending.get("extra", {})
        start, end = matches[n - 1]
        self._pending = None
        return self._apply_to_span(op, start, end, extra)

    def _resolve(self, op: str, word: str, extra: dict | None = None) -> ActionResult:
        """Find ``word`` (or phrase); act on it, or defer if several matches."""
        matches = _target_spans(word, self._tokens())
        if not matches:
            self._pending = None
            return ActionResult("not_found", message=f"„{word}“ nicht gefunden")
        if len(matches) == 1:
            self._pending = None
            s, e = matches[0]
            return self._apply_to_span(op, s, e, extra or {})
        # Several matches → number them and wait for "nimm N".
        self._pending = {"op": op, "matches": matches, "extra": extra or {}}
        return ActionResult("ambiguous", count=len(matches), matches=matches,
                            message=f"{len(matches)} Treffer – „nimm 1“ … "
                                    f"„nimm {len(matches)}“")

    def _apply_to_span(self, op: str, start: int, end: int,
                       extra: dict) -> ActionResult:
        if op == "cursor_before":
            self._set_cursor(start)
        elif op == "cursor_after":
            self._set_cursor(end)
        elif op == "select_word":
            self._set_cursor(end, start)
        elif op == "correct":
            self._set_cursor(end, start)
            self._awaiting_correction = True
            return ActionResult("awaiting_dictation",
                                message="sprich die Korrektur")
        elif op == "delete":
            self._set_cursor(end, start)
            self.te.textCursor().removeSelectedText()
            self._set_cursor(start)
        elif op == "replace":
            self._set_cursor(end, start)
            return self.insert_dictation(extra.get("to", ""))
        return ActionResult("ok")

    # -- navigation -----------------------------------------------------

    def _do_cursor_before(self, d): return self._resolve("cursor_before", d["word"])
    def _do_cursor_after(self, d): return self._resolve("cursor_after", d["word"])

    def _do_goto_start(self, _d):
        self._set_cursor(0)
        return ActionResult("ok")

    def _do_goto_end(self, _d):
        self._set_cursor(len(self._text()))
        return ActionResult("ok")

    def _do_line_start(self, _d):
        cur = self.te.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        self.te.setTextCursor(cur)
        return ActionResult("ok")

    def _do_line_end(self, _d):
        cur = self.te.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        self.te.setTextCursor(cur)
        return ActionResult("ok")

    # -- selection ------------------------------------------------------

    def _do_select_word(self, d): return self._resolve("select_word", d["word"])
    def _do_correct(self, d): return self._resolve("correct", d["word"])

    def _do_select_all(self, _d):
        self._set_cursor(len(self._text()), 0)
        return ActionResult("ok")

    def _do_select_range(self, d): return self._range("select", d)
    def _do_delete_range(self, d): return self._range("delete", d)

    def _range(self, op: str, d: dict) -> ActionResult:
        a = _target_spans(d["from"], self._tokens())
        b = _target_spans(d["to"], self._tokens())
        if not a or not b:
            missing = d["from"] if not a else d["to"]
            return ActionResult("not_found", message=f"„{missing}“ nicht gefunden")
        start = a[0][0]
        end = max(e for s, e in b if e >= start) if any(
            e >= start for s, e in b) else b[-1][1]
        self._set_cursor(end, start)
        if op == "delete":
            self.te.textCursor().removeSelectedText()
            self._set_cursor(start)
        return ActionResult("ok")

    def _block_span(self, which: str) -> tuple[int, int]:
        doc = self.te.document()
        if which == "last":
            block = doc.lastBlock()
            while block.isValid() and not block.text().strip() and block.previous().isValid():
                block = block.previous()
        else:
            block = self.te.textCursor().block()
        return block.position(), block.position() + block.length() - 1

    def _do_select_paragraph(self, d):
        s, e = self._block_span(d.get("which", "current"))
        self._set_cursor(e, s)
        return ActionResult("ok")

    def _do_delete_paragraph(self, d):
        s, e = self._block_span(d.get("which", "current"))
        self._set_cursor(e, s)
        self.te.textCursor().removeSelectedText()
        return ActionResult("ok")

    _do_select_line = _do_select_paragraph
    _do_delete_line = _do_delete_paragraph

    def _sentence_span(self, which: str) -> tuple[int, int]:
        text = self._text()
        pos = len(text) if which == "last" else self.te.textCursor().position()
        if which == "last":
            pos = len(text.rstrip())
        # start = just after the previous sentence terminator
        start = 0
        for m in re.finditer(r"[.!?…]+\s+", text[:pos]):
            start = m.end()
        # end = through the next terminator (inclusive)
        m = re.search(r"[.!?…]+", text[pos:])
        end = pos + m.end() if m else len(text)
        return start, end

    def _do_select_sentence(self, d):
        s, e = self._sentence_span(d.get("which", "current"))
        self._set_cursor(e, s)
        return ActionResult("ok")

    def _do_delete_sentence(self, d):
        s, e = self._sentence_span(d.get("which", "current"))
        self._set_cursor(e, s)
        self.te.textCursor().removeSelectedText()
        return ActionResult("ok")

    def _last_words_span(self, n: int) -> tuple[int, int] | None:
        pos = self.te.textCursor().position()
        toks = [(s, e) for s, e, _w in self._tokens() if e <= pos]
        if not toks:
            return None
        chosen = toks[-n:]
        return chosen[0][0], chosen[-1][1]

    def _do_select_last_words(self, d):
        span = self._last_words_span(int(d.get("n", 1)))
        if span is None:
            return ActionResult("info", message="keine Wörter")
        self._set_cursor(span[1], span[0])
        return ActionResult("ok")

    # -- deletion -------------------------------------------------------

    def _do_delete(self, d):
        target = d.get("target")
        if target == "word":
            return self._resolve("delete", d["word"])
        cur = self.te.textCursor()
        if cur.hasSelection():
            cur.removeSelectedText()
            self.te.setTextCursor(cur)
            return ActionResult("ok")
        # nothing selected → delete the previous word
        cur.movePosition(QTextCursor.MoveOperation.PreviousWord,
                         QTextCursor.MoveMode.KeepAnchor)
        cur.removeSelectedText()
        self.te.setTextCursor(cur)
        return ActionResult("ok")

    def _do_clear(self, _d):
        self.te.clear()
        self._last_insert = None
        return ActionResult("ok")

    # -- punctuation / formatting --------------------------------------

    def _do_punct(self, d):
        char = d.get("char", "")
        glue = d.get("glue")
        cur = self.te.textCursor()
        if glue != "left":
            # closing punctuation: remove a space directly before the cursor
            pos = cur.position()
            text = self._text()
            if pos > 0 and text[pos - 1] == " ":
                cur.deletePreviousChar()
        cur.insertText(char)
        self.te.setTextCursor(cur)
        return ActionResult("ok")

    def _do_newline(self, _d):
        self.te.textCursor().insertText("\n")
        return ActionResult("ok")

    def _do_paragraph(self, _d):
        self.te.textCursor().insertText("\n\n")
        return ActionResult("ok")

    def _do_capitalize(self, d):
        mode = d.get("mode", "upper")
        cur = self.te.textCursor()
        if not cur.hasSelection():
            cur.movePosition(QTextCursor.MoveOperation.PreviousWord,
                             QTextCursor.MoveMode.KeepAnchor)
        sel = cur.selectedText()
        if not sel:
            return ActionResult("info", message="nichts zum Ändern")
        new = (sel[0].upper() if mode == "upper" else sel[0].lower()) + sel[1:]
        cur.insertText(new)
        self.te.setTextCursor(cur)
        return ActionResult("ok")

    # -- correction / history ------------------------------------------

    def _do_correct_last(self, _d):
        if not self._last_insert:
            return ActionResult("info", message="nichts zu korrigieren")
        s, e = self._last_insert
        self._set_cursor(e, s)
        self._awaiting_correction = True
        return ActionResult("awaiting_dictation", message="sprich die Korrektur")

    def _do_replace(self, d):
        return self._resolve("replace", d["from"], {"to": d["to"]})

    def _do_undo(self, _d):
        self.te.undo()
        return ActionResult("ok")

    def _do_redo(self, _d):
        self.te.redo()
        return ActionResult("ok")

    def _do_literal(self, d):
        return self.insert_dictation(d.get("text", ""))
