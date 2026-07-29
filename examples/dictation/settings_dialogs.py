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
        rows = list(self._rows_provider())
        if not rows:
            if self._empty_text:
                item = QListWidgetItem(self._empty_text)
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
