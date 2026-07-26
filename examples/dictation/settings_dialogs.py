"""Pop-out list editors for the glossary and the error memory.

A small, reusable dialog: an optional "add" field on top, then the entries
listed one per row, each with an "✕" button to remove it.  Decoupled via
callbacks (``rows_provider`` / ``on_add`` / ``on_remove`` / ``on_clear``) so it
is unit-testable offscreen without the rest of the module.
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


class ListEditorDialog(QDialog):
    """Add/remove editor for a list of (display, key) rows."""

    def __init__(
        self,
        title: str,
        rows_provider: Callable[[], list[tuple[str, str]]],
        on_remove: Callable[[str], None],
        on_add: Callable[[str], None] | None = None,
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
        self.resize(440, 480)
        self._rows_provider = rows_provider
        self._on_remove = on_remove
        self._on_add = on_add
        self._on_clear = on_clear
        self._empty_text = empty_text

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        if intro:
            lbl = QLabel(intro)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: palette(mid);")
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
        for display, key in rows:
            item = QListWidgetItem()
            self._list.addItem(item)
            widget = self._row_widget(display, key)
            item.setSizeHint(widget.sizeHint())
            self._list.setItemWidget(item, widget)

    def _row_widget(self, display: str, key: str) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(6, 2, 6, 2)
        label = QLabel(display)
        label.setWordWrap(True)
        row.addWidget(label, 1)
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

    def _remove(self, key: str) -> None:
        self._on_remove(key)
        self._reload()

    def _clear_all(self) -> None:
        if self._on_clear is not None:
            self._on_clear()
            self._reload()
