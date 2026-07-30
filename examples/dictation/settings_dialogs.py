"""Pop-out list editors for the glossary and the error memory.

A small, reusable dialog: an optional "add" field on top, then the entries
listed one per row.  Each row can show a static label, an editable value field
and an "✕" button to remove it.  Decoupled via callbacks (``rows_provider`` /
``on_add`` / ``on_remove`` / ``on_edit`` / ``on_clear``) so it is unit-testable
offscreen without the rest of the module.

Rows are ``(key, label, value)``:
  * ``key``    – stable identifier passed back to the callbacks
  * ``label``  – static text shown before the value (e.g. "kaser  →"); may be ""
  * ``value``  – the editable text (when ``on_edit`` is set) or plain text
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import vocabulary as vocab

_READABLE = "color: palette(windowText);"


class EnrollmentDialog(QDialog):
    """Guided reading: shows a known sentence, records the user reading it, and
    stores (audio, exact text) gold pairs via the ``on_start`` / ``on_stop``
    callbacks (which the module implements with its recorder)."""

    def __init__(self, prompts: list[str],
                 on_start: Callable[[], bool],
                 on_stop: Callable[[str], str],
                 on_discard: Callable[[str], None] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prompts = list(prompts)
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_discard = on_discard or (lambda _s: None)
        self._index = 0
        self._recording = False
        self._saved: dict[int, str] = {}    # sentence index → saved sample id
        self.setWindowTitle("Stimm-Training (Vorlesen)")
        self.resize(560, 320)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel("Lies den Satz laut und deutlich vor. „Aufnahme starten“ "
                       "→ vorlesen → „Stopp“. Die Aufnahme wird zusammen mit dem "
                       "genauen Text gespeichert (für spätere Stimm-Anpassung).")
        intro.setWordWrap(True)
        intro.setStyleSheet(_READABLE)
        layout.addWidget(intro)

        self._prompt = QLabel()
        self._prompt.setWordWrap(True)
        self._prompt.setStyleSheet(
            "font-size: 16pt; font-weight: bold; color: palette(windowText);")
        layout.addWidget(self._prompt, 1)

        self._progress = QLabel()
        self._progress.setStyleSheet(_READABLE)
        layout.addWidget(self._progress)

        row = QHBoxLayout()
        self._back_btn = QPushButton("◀ Zurück (neu aufnehmen)")
        self._back_btn.setToolTip("Zum vorigen Satz – falls du dich versprochen "
                                  "hast, dort neu aufnehmen (ersetzt die alte "
                                  "Aufnahme).")
        self._back_btn.clicked.connect(self._back)
        row.addWidget(self._back_btn)
        self._record_btn = QPushButton("● Aufnahme starten")
        self._record_btn.clicked.connect(self._toggle)
        row.addWidget(self._record_btn)
        skip = QPushButton("Überspringen ▸")
        skip.clicked.connect(self._next)
        row.addWidget(skip)
        row.addStretch()
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self._update()

    def _update(self) -> None:
        marker = "  ✓ (aufgenommen)" if self._index in self._saved else ""
        self._prompt.setText("„" + self._prompts[self._index] + "“" + marker)
        self._progress.setText(
            f"Satz {self._index + 1} von {len(self._prompts)}  ·  "
            f"aufgenommen: {len(self._saved)}")
        self._back_btn.setEnabled(not self._recording and self._index > 0)

    def _toggle(self) -> None:
        if not self._recording:
            if self._on_start():
                self._recording = True
                self._record_btn.setText("■ Stopp & speichern")
                self._update()
        else:
            stamp = self._on_stop(self._prompts[self._index])
            self._recording = False
            self._record_btn.setText("● Aufnahme starten")
            if stamp:
                old = self._saved.get(self._index)
                if old:
                    self._on_discard(old)       # replace the previous take
                self._saved[self._index] = stamp
                self._advance()
            self._update()

    def _next(self) -> None:
        if not self._recording:
            self._advance()
            self._update()

    def _back(self) -> None:
        if not self._recording and self._index > 0:
            self._index -= 1
            self._update()

    def _advance(self) -> None:
        self._index = (self._index + 1) % len(self._prompts)

    def reject(self) -> None:  # noqa: D102 (Qt override)
        if self._recording:            # stop + discard a half-read take
            stamp = self._on_stop(self._prompts[self._index])
            if stamp:
                self._on_discard(stamp)
            self._recording = False
        super().reject()


class LearnFromTextDialog(QDialog):
    """Extract likely vocabulary from a pasted text or a file, let the user pick
    which terms to keep, then hand them back via ``on_accept(list)``."""

    def __init__(self, on_accept: Callable[[list[str]], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_accept = on_accept
        self.setWindowTitle("Aus Text lernen")
        self.resize(520, 560)

        layout = QVBoxLayout(self)
        intro = QLabel("Füge einen Text ein (oder lade eine Datei) – WithEase "
                       "schlägt daraus deine Fachbegriffe/Namen vor. Häkchen "
                       "setzen und übernehmen; sie landen in „Eigene Wörter“.")
        intro.setWordWrap(True)
        intro.setStyleSheet(_READABLE)
        layout.addWidget(intro)

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText("Text hier einfügen …")
        layout.addWidget(self._text, 1)

        top = QHBoxLayout()
        file_btn = QPushButton("Datei laden …")
        file_btn.clicked.connect(self._load_file)
        top.addWidget(file_btn)
        analyse_btn = QPushButton("Analysieren")
        analyse_btn.clicked.connect(self._analyse)
        top.addWidget(analyse_btn)
        top.addStretch()
        layout.addLayout(top)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        take = QPushButton("Ausgewählte übernehmen")
        take.clicked.connect(self._accept)
        footer.addWidget(take)
        layout.addLayout(footer)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Textdatei wählen", "", "Text (*.txt *.md *.csv);;Alle (*.*)")
        if path:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    self._text.setPlainText(f.read())
            except OSError:
                pass
            self._analyse()

    def _analyse(self) -> None:
        self._list.clear()
        for term in vocab.extract_terms(self._text.toPlainText()):
            item = QListWidgetItem(term)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        if self._list.count() == 0:
            item = QListWidgetItem("Keine Begriffe gefunden.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)

    def _accept(self) -> None:
        chosen = [self._list.item(i).text() for i in range(self._list.count())
                  if self._list.item(i).checkState() == Qt.CheckState.Checked]
        if chosen:
            self._on_accept(chosen)
        self.accept()


class ListEditorDialog(QDialog):
    """Add / edit / remove editor for a list of ``(key, label, value)`` rows."""

    def __init__(
        self,
        title: str,
        rows_provider: Callable[[], list[tuple[str, str, str]]],
        on_remove: Callable[[str], None],
        on_add: Callable[..., None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
        add_placeholder: str = "",
        add_placeholder2: str = "",
        add_label: str = "Hinzufügen",
        intro: str = "",
        empty_text: str = "",
        clear_label: str = "",
        on_clear: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 500)
        self._rows_provider = rows_provider
        self._on_remove = on_remove
        self._on_add = on_add
        self._on_edit = on_edit
        self._on_clear = on_clear
        self._empty_text = empty_text

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        if intro:
            lbl = QLabel(intro)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(_READABLE)
            layout.addWidget(lbl)

        self._input2 = None
        if on_add is not None:
            add_row = QHBoxLayout()
            self._input = QLineEdit()
            self._input.setPlaceholderText(add_placeholder)
            self._input.returnPressed.connect(self._add)
            add_row.addWidget(self._input, 1)
            if add_placeholder2:
                self._input2 = QLineEdit()
                self._input2.setPlaceholderText(add_placeholder2)
                self._input2.returnPressed.connect(self._add)
                add_row.addWidget(self._input2, 1)
            add_btn = QPushButton(add_label)
            add_btn.clicked.connect(self._add)
            add_row.addWidget(add_btn)
            layout.addLayout(add_row)
        else:
            self._input = None

        self._search = QLineEdit()
        self._search.setPlaceholderText("Suchen …")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda *_: self._reload())
        layout.addWidget(self._search)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        footer = QHBoxLayout()
        if on_clear is not None:
            clear_btn = QPushButton(clear_label or "Alle löschen")
            clear_btn.clicked.connect(self._clear_all)
            footer.addWidget(clear_btn)
        footer.addStretch()
        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._reload()

    # -- population -----------------------------------------------------

    def _reload(self) -> None:
        self._list.clear()
        query = self._search.text().strip().casefold()
        all_rows = list(self._rows_provider())
        rows = [r for r in all_rows if not query or query in
                f"{r[0]} {r[1]} {r[2]}".casefold()]
        if not rows:
            if all_rows and query:
                note = "Keine Treffer."
            else:
                note = self._empty_text
            if note:
                item = QListWidgetItem(note)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self._list.addItem(item)
            return
        # A single width for the left "key" column so keys, arrows and value
        # fields line up in columns across all rows.
        labels = [lb for _k, lb, _v in rows if lb]
        key_width = 0
        if labels:
            fm = self.fontMetrics()
            key_width = min(220, max(fm.horizontalAdvance(x) for x in labels) + 8)
        for key, label, value in rows:
            item = QListWidgetItem()
            self._list.addItem(item)
            widget = self._row_widget(key, label, value, key_width)
            item.setSizeHint(widget.sizeHint())
            self._list.setItemWidget(item, widget)

    def _row_widget(self, key: str, label: str, value: str,
                    key_width: int = 0) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(8)
        if label:
            lbl = QLabel(label)
            lbl.setStyleSheet(_READABLE)
            if key_width:
                lbl.setFixedWidth(key_width)
            row.addWidget(lbl)
            arrow = QLabel("→")
            arrow.setStyleSheet(_READABLE)
            row.addWidget(arrow)
        if self._on_edit is not None:
            field = QLineEdit(value)
            field.setStyleSheet(_READABLE)
            field.editingFinished.connect(
                lambda k=key, f=field: self._edit(k, f.text()))
            row.addWidget(field, 1)
        else:
            text = QLabel(value)
            text.setWordWrap(True)
            text.setStyleSheet(_READABLE)
            row.addWidget(text, 1)
        remove = QPushButton("✕")
        remove.setFixedWidth(30)
        remove.setToolTip("Entfernen")
        remove.clicked.connect(lambda _=False, k=key: self._remove(k))
        row.addWidget(remove)
        return widget

    # -- actions --------------------------------------------------------

    def _add(self) -> None:
        if self._on_add is None or self._input is None:
            return
        text = self._input.text().strip()
        if not text:
            return
        if self._input2 is not None:
            self._on_add(text, self._input2.text().strip())
            self._input2.clear()
        else:
            self._on_add(text)
        self._input.clear()
        self._reload()

    def _edit(self, key: str, new_value: str) -> None:
        if self._on_edit is not None:
            self._on_edit(key, new_value)
            self._reload()

    def _remove(self, key: str) -> None:
        self._on_remove(key)
        self._reload()

    def _clear_all(self) -> None:
        if self._on_clear is not None:
            self._on_clear()
            self._reload()
