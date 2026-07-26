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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_READABLE = "color: palette(windowText);"


class ListEditorDialog(QDialog):
    """Add / edit / remove editor for a list of ``(key, label, value)`` rows."""

    def __init__(
        self,
        title: str,
        rows_provider: Callable[[], list[tuple[str, str, str]]],
        on_remove: Callable[[str], None],
        on_add: Callable[[str], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
        add_placeholder: str = "",
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

        if on_add is not None:
            add_row = QHBoxLayout()
            self._input = QLineEdit()
            self._input.setPlaceholderText(add_placeholder)
            self._input.returnPressed.connect(self._add)
            add_row.addWidget(self._input, 1)
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
        if text:
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
