"""Aussprache anlernen: say a dictionary word, keep how it was misheard.

A recogniser cannot be retrained with a few words - that takes hours of
speech.  What a few takes CAN show is how *this* recogniser writes *this*
word in *your* voice through *your* microphone: "Parakit", "Para Kid".  Those
spellings are then kept on the dictionary entry and replaced exactly,
instead of guessing from similarity.

The recordings stay in memory and are gone when the dialog closes; only the
text of the mishearings is kept.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dict_i18n import t as _t

TAKES = 3

_READABLE = "color: palette(windowText);"


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").casefold()


def clean_variant(text: str) -> str:
    """What a recogniser wrote, without the sentence dressing around it."""
    text = " ".join((text or "").split())
    return text.strip(" .,;:!?…\"'„“”‚‘’»«()[]")


# The most frequent German words beyond the function words - a mishearing
# that lands on one of these would replace ordinary text.
_FREQUENT = """
viel viele vielen mehr sehr gut gute guten besser neu neue neuen alt alte groß
große großen klein kleine lang lange kurz hoch weit wieder immer nie oft schon
noch nur auch jetzt heute morgen gestern hier dort da dann denn doch mal ganz
gleich gern gerne bitte danke ja nein vielleicht wirklich einfach genau
richtig falsch schnell langsam leicht schwer früh spät bald später zeit tag
tage woche monat jahr jahre uhr stunde minute mal leute mensch menschen mann
frau kind kinder freund freunde familie haus hause wohnung zimmer tür tisch
stuhl bett auto bus bahn zug weg straße stadt land welt geld arbeit firma
schule problem frage antwort idee sache ding dinge teil ende anfang seite
name nummer liste text brief mail nachricht rechnung termin bild film spiel
musik buch wasser essen kaffee tee brot hand hände kopf auge augen herz
machen macht gemacht sagen sagt gesagt gehen geht gegangen kommen kommt
gekommen sehen sieht gesehen geben gibt gegeben nehmen nimmt genommen
wissen weiß gewusst finden findet gefunden denken denkt gedacht glauben
lassen lässt stehen steht liegen liegt bleiben bleibt heißen heißt halten
hält bringen bringt leben lebt spielen spielt zeigen zeigt fragen fragt
brauchen braucht arbeiten arbeitet schreiben schreibt lesen liest hören
hört sprechen spricht laufen läuft fahren fährt kaufen kauft suchen sucht
warten wartet helfen hilft versuchen legen setzen stellen öffnen schließen
viel kiel hut ist hat war sind sein haben werden wird kann muss soll will
darf möchte könnte würde hätte wäre gibt geht kommt sagt macht
""".split()

_common_words: set[str] | None = None


def _common() -> set[str]:
    """Ordinary German words, from the texts WithEase already carries.

    A mishearing that is an ordinary word ("viel" for "Kiel") would replace
    every real use of that word - such a variant is offered unticked."""
    global _common_words
    if _common_words is None:
        words: set[str] = set()
        try:
            from postprocess import _LOWER_WORDS
            words |= set(_LOWER_WORDS)
        except Exception:
            pass
        try:
            import dict_i18n
            for value in dict_i18n.STRINGS.get("de", {}).values():
                words |= {w.casefold() for w in re.findall(r"[^\W\d_]+", value)}
        except Exception:
            pass
        try:
            import streaming
            words |= set(streaming._DE)
        except Exception:
            pass
        words |= set(_FREQUENT) - {"kiel"}
        _common_words = words
    return _common_words


def risky(variant: str, own_texts: list[str] | tuple = ()) -> bool:
    """True when replacing ``variant`` would also hit ordinary text: it is a
    frequent German word, or it already appears in what you dictated."""
    words = re.findall(r"[^\W\d_]+", variant or "")
    if not words or len(_key(variant)) < 3:
        return True
    if all(w.casefold() in _common() for w in words):
        return True
    pattern = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, variant.split()))
                         + r"(?!\w)", re.IGNORECASE)
    return any(pattern.search(text or "") for text in own_texts)


def evaluate(word: str, heard: list[tuple[str, str]],
             own_texts: list[str] | tuple = ()) -> list[dict]:
    """Group what the recognisers wrote.

    ``heard`` is ``[(engine, text), ...]``.  Returns one row per distinct
    spelling: ``{"text", "engines", "count", "correct", "risky"}``."""
    rows: dict[str, dict] = {}
    for engine, text in heard:
        variant = clean_variant(text)
        if not variant:
            continue
        key = _key(variant)
        row = rows.setdefault(key, {"text": variant, "engines": [],
                                    "count": 0,
                                    "correct": key == _key(word),
                                    "risky": False})
        row["count"] += 1
        if engine not in row["engines"]:
            row["engines"].append(engine)
    for row in rows.values():
        row["risky"] = not row["correct"] and risky(row["text"], own_texts)
    return sorted(rows.values(), key=lambda r: (r["correct"], -r["count"]))


class _Bridge(QObject):
    status = Signal(str)
    level = Signal(float)
    take = Signal(int)
    heard = Signal(list)
    failed = Signal(str)


class PronunciationDialog(QDialog):
    """Record a word a few times and choose which mishearings to keep."""

    # texts are looked up as "<prefix>.<key>", falling back to "pron.<key>"
    PREFIX = "pron"

    def __init__(self, word: str, module: Any,
                 on_saved: Callable[[], None] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._word = word
        self._module = module
        self._on_saved = on_saved
        self._capture: Any = None
        self._rows: list[tuple[QCheckBox | None, dict]] = []
        self.setWindowTitle(self._tt("title", word=word))
        self.resize(560, 460)

        self._bridge = _Bridge()
        self._bridge.status.connect(self._set_status)
        self._bridge.level.connect(self._set_level)
        self._bridge.take.connect(self._take_done)
        self._bridge.heard.connect(self._show_results)
        self._bridge.failed.connect(self._show_failure)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel(self._tt("intro", word=word, n=str(TAKES)))
        intro.setWordWrap(True)
        intro.setStyleSheet(_READABLE)
        layout.addWidget(intro)
        self._intro = intro

        self._status = QLabel("")
        self._status.setWordWrap(True)
        font = self._status.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        self._status.setFont(font)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, TAKES)
        self._progress.setFormat(self._tt("progress"))
        layout.addWidget(self._progress)
        self._level = QProgressBar()
        self._level.setRange(0, 100)
        self._level.setTextVisible(False)
        self._level.setMaximumHeight(10)
        layout.addWidget(self._level)

        self._results = QWidget()
        self._results_layout = QVBoxLayout(self._results)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._results)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self._start = QPushButton(self._tt("start"))
        self._start.clicked.connect(self._begin)
        buttons.addWidget(self._start)
        buttons.addStretch()
        self._save = QPushButton(self._tt("save"))
        self._save.clicked.connect(self._save_variants)
        self._save.setEnabled(False)
        buttons.addWidget(self._save)
        close = QPushButton(_t("dlg.close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        try:
            from withease.gui import theme
            for button in (self._start, self._save, close):
                button.setMinimumHeight(theme.target_px())
        except Exception:
            pass
        # Enter does the obvious next thing: start the recording - and, once
        # there is something to keep, apply it.  Opening the dialog and then
        # having to find the button with the mouse broke a flow that is
        # otherwise all voice and Enter.
        for button in (self._start, self._save, close):
            button.setAutoDefault(button is not close)
        self._start.setDefault(True)

        self._set_status(self._tt("ready"))

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        button = self._save if self._save.isEnabled() else self._start
        button.setDefault(True)
        button.setFocus()

    def _tt(self, key: str, **kwargs: str) -> str:
        import dict_i18n
        own = f"{self.PREFIX}.{key}"
        known = any(own in table for table in dict_i18n.STRINGS.values())
        return _t(own if known else f"pron.{key}", **kwargs)

    # -- what a subclass changes --------------------------------------------

    def _evaluate(self, heard: list, own: list[str]) -> list[dict]:
        return evaluate(self._word, heard, own)

    def _store(self, variants: list[str]) -> None:
        self._module.add_heard_variants(self._word, variants)

    # -- recording --------------------------------------------------------

    def _begin(self) -> None:
        self._clear_results()
        self._start.setEnabled(False)
        self._save.setEnabled(False)
        self._progress.setValue(0)
        self._set_status(self._tt("preparing"))
        bridge = self._bridge

        def run() -> None:
            try:
                engines = self._module.pronunciation_engines()
            except Exception as exc:
                bridge.failed.emit(str(exc)[:200])
                return
            if not engines:
                bridge.failed.emit(self._tt("no_engine"))
                return
            bridge.status.emit(self._tt("speak", word=self._word, i="1",
                                  n=str(TAKES)))
            takes: list[bytes] = []
            done = threading.Event()
            self._waiting = done          # closing the dialog ends the wait

            def on_take(pcm: bytes) -> None:
                takes.append(pcm)
                bridge.take.emit(len(takes))
                if len(takes) >= TAKES:
                    done.set()
                else:
                    bridge.status.emit(self._tt("speak", word=self._word,
                                          i=str(len(takes) + 1), n=str(TAKES)))
            try:
                self._capture = self._module.capture_takes(
                    on_take, lambda v: bridge.level.emit(v))
            except Exception as exc:
                bridge.failed.emit(str(exc)[:200])
                return
            finished = done.wait(60)
            capture, self._capture = self._capture, None
            if capture is not None:
                capture.stop()
            if not finished and not takes:
                bridge.failed.emit(self._tt("nothing"))
                return
            bridge.status.emit(self._tt("recognising"))
            heard = []
            for pcm in takes[:TAKES]:
                for name, recognise in engines:
                    try:
                        heard.append((name, recognise(pcm)))
                    except Exception:
                        pass
            bridge.heard.emit(heard)

        threading.Thread(target=run, daemon=True).start()

    def _take_done(self, n: int) -> None:
        self._progress.setValue(n)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _set_level(self, value: float) -> None:
        self._level.setValue(int(max(0.0, min(1.0, value)) * 100))

    def _show_failure(self, message: str) -> None:
        self._set_status(message)
        self._start.setEnabled(True)
        self._start.setText(self._tt("again"))
        self._level.setValue(0)

    # -- choosing ---------------------------------------------------------

    def _clear_results(self) -> None:
        self._rows = []
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _show_results(self, heard: list) -> None:
        self._level.setValue(0)
        self._start.setEnabled(True)
        self._start.setText(self._tt("again"))
        try:
            own = list(self._module.dictated_texts())
        except Exception:
            own = []
        rows = self._evaluate(heard, own)
        if not rows:
            self._set_status(self._tt("nothing"))
            return
        wrong = [r for r in rows if not r["correct"] and not r.get("note")]
        self._set_status(self._tt("all_right") if not wrong
                         else self._tt("choose"))
        for row in rows:
            engines = ", ".join(row["engines"])
            if row.get("note"):             # cannot be kept - says why
                label = QLabel(row["note"])
                label.setWordWrap(True)
                label.setStyleSheet(_READABLE)
                self._results_layout.addWidget(label)
                self._rows.append((None, row))
                continue
            if row["correct"]:
                label = QLabel(self._tt("row.correct", text=row["text"],
                                  engines=engines, n=str(row["count"])))
                label.setWordWrap(True)
                label.setStyleSheet(_READABLE)
                self._results_layout.addWidget(label)
                self._rows.append((None, row))
                continue
            box = QCheckBox(self._tt("row.variant", text=row["text"],
                               engines=engines, n=str(row["count"])))
            box.setChecked(not row["risky"])
            self._results_layout.addWidget(box)
            if row["risky"]:
                note = QLabel(self._tt("row.risky", text=row["text"]))
                note.setWordWrap(True)
                note.setIndent(28)
                note.setStyleSheet(_READABLE)
                self._results_layout.addWidget(note)
            self._rows.append((box, row))
        self._results_layout.addStretch()
        self._save.setEnabled(bool(wrong))
        if wrong:               # Enter keeps what was ticked
            self._save.setDefault(True)
            self._save.setFocus()

    def chosen(self) -> list[str]:
        return [row["text"] for box, row in self._rows
                if box is not None and box.isChecked()]

    def _save_variants(self) -> None:
        variants = self.chosen()
        self._store(variants)
        self._start.setDefault(True)        # Enter records again
        self._start.setFocus()
        if self._on_saved is not None:
            self._on_saved()
        self._set_status(self._tt("saved", n=str(len(variants))))
        self._save.setEnabled(False)

    def done(self, result: int) -> None:
        waiting = getattr(self, "_waiting", None)
        if waiting is not None:
            waiting.set()
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()
        super().done(result)


class CommandTrainingDialog(PronunciationDialog):
    """Befehl einlernen: say a command a few times, and the way the
    recogniser writes it in YOUR voice counts as that command from then on.

    The same recording and choosing as for a dictionary word; only what is
    kept differs - a spelling that stands for a command, not a word to
    replace inside the text."""

    PREFIX = "cmd"

    def __init__(self, module: Any, phrase: str = "",
                 parent: QWidget | None = None) -> None:
        import commands_de as cde
        self._cde = cde
        self._choices = cde.trainable_commands()
        phrase = phrase or (self._choices[0] if self._choices else "")
        super().__init__(phrase, module, parent=parent)
        self.setWindowTitle(self._tt("title"))
        self.resize(600, 580)           # room for the picker and the list
        from PySide6.QtWidgets import QComboBox
        row = QHBoxLayout()
        row.addWidget(QLabel(self._tt("pick")))
        self._pick = QComboBox()
        for choice in self._choices:
            self._pick.addItem(choice, choice)
        index = self._pick.findData(phrase)
        self._pick.setCurrentIndex(max(0, index))
        self._pick.currentIndexChanged.connect(self._on_pick)
        row.addWidget(self._pick, 1)
        self.layout().insertLayout(0, row)
        self._known = QLabel("")
        self._known.setWordWrap(True)
        self._known.setStyleSheet(_READABLE)
        self._known.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._known.linkActivated.connect(self._forget)
        self.layout().insertWidget(2, self._known)
        self._show_known()

    def _on_pick(self, _index: int) -> None:
        self._word = self._pick.currentData() or ""
        self._intro.setText(self._tt("intro", word=self._word, n=str(TAKES)))
        self._clear_results()
        self._save.setEnabled(False)
        self._set_status(self._tt("ready"))
        self._show_known()

    def _show_known(self) -> None:
        """The spellings already kept for this command, each removable."""
        mine = self._module.command_variants(self._word)
        if not mine:
            self._known.setText(self._tt("known.none"))
            return
        import html
        links = " · ".join(
            f"„{html.escape(v)}“ <a href=\"{html.escape(v)}\">"
            f"{html.escape(self._tt('known.remove'))}</a>" for v in mine)
        self._known.setText(self._tt("known", list=links))

    def _forget(self, variant: str) -> None:
        self._module.remove_command_variant(variant)
        self._show_known()

    def _evaluate(self, heard: list, own: list[str]) -> list[dict]:
        cde = self._cde
        target = cde._parse_exact(self._word)
        rows: dict[str, dict] = {}
        for engine, text in heard:
            variant = clean_variant(text)
            if not variant:
                continue
            key = cde.normalise(variant)
            if not key:
                continue
            row = rows.setdefault(key, {"text": variant, "engines": [],
                                        "count": 0, "correct": False,
                                        "risky": False})
            row["count"] += 1
            if engine not in row["engines"]:
                row["engines"].append(engine)
        for key, row in rows.items():
            said = cde.parse(row["text"])
            if said == target:
                row["correct"] = True       # already heard as this command
            elif said is not None:
                row["note"] = self._tt("row.other", text=row["text"])
            else:
                # one ordinary word as a command would fire on that word
                row["risky"] = (len(key.split()) == 1
                                and key in _common())
        return sorted(rows.values(),
                      key=lambda r: (r["correct"], bool(r.get("note")),
                                     -r["count"]))

    def _store(self, variants: list[str]) -> None:
        self._module.add_command_variants(self._word, variants)
        self._show_known()
