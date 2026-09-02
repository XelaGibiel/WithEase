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

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import vocabulary as vocab
from dict_i18n import t as _t

_READABLE = "color: palette(windowText);"


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


class LearnFromTextDialog(QDialog):
    """Extract likely vocabulary from a pasted text or a file, let the user pick
    which terms to keep, then hand them back via ``on_accept(list)``."""

    def __init__(self, on_accept: Callable[[list[str]], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_accept = on_accept
        self.setWindowTitle(_t("learn.title"))
        self.resize(520, 560)

        layout = QVBoxLayout(self)
        intro = QLabel(_t("learn.intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(_READABLE)
        layout.addWidget(intro)

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(_t("learn.placeholder"))
        layout.addWidget(self._text, 1)

        top = QHBoxLayout()
        file_btn = QPushButton(_t("dlg.learn.file"))
        file_btn.clicked.connect(self._load_file)
        top.addWidget(file_btn)
        analyse_btn = QPushButton(_t("dlg.analyse"))
        analyse_btn.clicked.connect(self._analyse)
        top.addWidget(analyse_btn)
        top.addStretch()
        layout.addLayout(top)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton(_t("learn.cancel"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        take = QPushButton(_t("learn.accept"))
        take.clicked.connect(self._accept)
        footer.addWidget(take)
        layout.addLayout(footer)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, _t("dlg.learn.choose"), "", _t("dlg.filter.learn"))
        if path:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    self._text.setPlainText(f.read())
            except OSError:
                pass
            self._analyse()

    def _analyse(self) -> None:
        self._list.clear()
        scored = vocab.extract_terms_scored(self._text.toPlainText())
        for term, strong in scored:
            item = QListWidgetItem(term)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # pre-check only confident terms (names/CamelCase/acronyms); plain
            # capitalised German words are left unchecked (usually just nouns).
            item.setCheckState(Qt.CheckState.Checked if strong
                               else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        if self._list.count() == 0:
            item = QListWidgetItem(_t("learn.none"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
        else:
            hint = QListWidgetItem(_t("dlg.learn.tip"))
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.insertItem(0, hint)

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
        add_label: str = _t("dlg.add"),
        intro: str = "",
        empty_text: str = "",
        clear_label: str = "",
        on_clear: Callable[[], None] | None = None,
        combined_search: bool = False,
        on_export: Callable[[str], int] | None = None,
        on_import: Callable[[str], int] | None = None,
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
        # combined_search: the first add field doubles as the live search box,
        # so typing a word instantly filters the list (you see if it exists).
        self._combined_search = combined_search and on_add is not None
        self._on_export = on_export
        self._on_import = on_import
        self._exists_label = None

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

        if self._combined_search:
            # first field is also the search box → live-filter + "exists" hint
            self._input.setClearButtonEnabled(True)
            self._input.textChanged.connect(self._on_input_changed)
            self._search = None
            self._exists_label = QLabel("")
            self._exists_label.setStyleSheet(_READABLE)
            layout.addWidget(self._exists_label)
        else:
            self._search = QLineEdit()
            self._search.setPlaceholderText("Suchen …")
            self._search.setClearButtonEnabled(True)
            self._search.textChanged.connect(lambda *_: self._reload())
            layout.addWidget(self._search)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        footer = QHBoxLayout()
        if on_clear is not None:
            clear_btn = QPushButton(clear_label or _t("dlg.clear_all"))
            _mark_danger(clear_btn)
            clear_btn.clicked.connect(self._clear_all)
            footer.addWidget(clear_btn)
        if on_import is not None:
            import_btn = QPushButton("Import …")
            import_btn.setToolTip(_wrap_tip(_t("dlg.import.hint.long")))
            import_btn.clicked.connect(self._import)
            footer.addWidget(import_btn)
        if on_export is not None:
            export_btn = QPushButton("Export …")
            export_btn.setToolTip(_wrap_tip(_t("dlg.export.hint")))
            export_btn.clicked.connect(self._export)
            footer.addWidget(export_btn)
        footer.addStretch()
        close_btn = QPushButton(_t("dlg.close"))
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._reload()

    def _query(self) -> str:
        box = self._input if self._combined_search else self._search
        return box.text().strip().casefold() if box is not None else ""

    def _on_input_changed(self, *_a) -> None:
        self._reload()
        if self._exists_label is None or self._input is None:
            return
        typed = self._input.text().strip().casefold()
        if not typed:
            self._exists_label.setText("")
            return
        existing = None
        for key, _label, value in self._rows_provider():
            if key.casefold() == typed:
                existing = value
                break
        if existing is not None:
            self._exists_label.setText(f"✓ existiert bereits  →  {existing}")
        else:
            self._exists_label.setText(_t("dlg.vocab.new"))

    def _export(self) -> None:
        if self._on_export is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, _t("dlg.export"), "woerterbuch.txt",
            _t("dlg.filter.text"))
        if path:
            try:
                self._on_export(path)
            except Exception:
                pass

    def _import(self) -> None:
        if self._on_import is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, _t("dlg.import"), "",
            _t("dlg.filter.import"))
        if path:
            try:
                self._on_import(path)
            except Exception:
                pass
            if self._input is not None:
                self._input.clear()
            self._reload()

    # -- population -----------------------------------------------------

    def _reload(self) -> None:
        self._list.clear()
        query = self._query()
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
        remove.setToolTip(_wrap_tip(_t("dlg.remove")))
        remove.setProperty("dangerIcon", True)      # red ✕ (theme QSS)
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


class AiActionsDialog(QDialog):
    """Manage the user's „KI-Aktionen“: a list of (name, prompt) buttons that
    appear in the dictation window.  Left: the list; right: name + prompt.

    The four class attributes below are the only wording differences to the
    text-snippet editor, which subclasses this instead of copying it."""

    # Keys, not texts: a class body runs at IMPORT time, so a translated
    # string here would freeze the language of that moment.
    TITLE_KEY = "dlg.ai.title"
    INTRO_KEY = "dlg.ai.intro.long"
    NAME_KEY = "dlg.ai.name"
    BODY_KEY = "dlg.ai.body"
    BODY_PLACEHOLDER_KEY = "dlg.ai.body.placeholder"

    def __init__(self, actions: list, on_save: Callable[[list], None],
                 select_index: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t(self.TITLE_KEY))
        self.resize(640, 460)
        self._actions = [dict(a) for a in actions]     # working copy
        self._on_save = on_save
        self._cur = -1

        layout = QVBoxLayout(self)
        intro = QLabel(_t(self.INTRO_KEY))
        intro.setWordWrap(True)
        intro.setStyleSheet(_READABLE)
        layout.addWidget(intro)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self._list = QListWidget()
        # Match the dictation-history list style (rounded card, alternating
        # rows, divider between entries, accent selection instead of the raw
        # orange highlight) via the shared #dictHistory QSS in theme.py.
        self._list.setObjectName("dictHistory")
        self._list.setAlternatingRowColors(True)
        self._list.currentRowChanged.connect(self._select)
        left.addWidget(self._list, 1)
        lbtn = QHBoxLayout()
        addb = QPushButton(_t("dlg.new"))
        addb.clicked.connect(self._add)
        lbtn.addWidget(addb)
        self._delb = _mark_danger(QPushButton(_t("dlg.remove")))
        self._delb.clicked.connect(self._remove)
        lbtn.addWidget(self._delb)
        left.addLayout(lbtn)
        mbtn = QHBoxLayout()          # reorder → the window buttons follow suit
        self._upb = QPushButton(_t("dlg.up"))
        self._upb.setToolTip(_wrap_tip(_t("dlg.up.hint")))
        self._upb.clicked.connect(lambda: self._move(-1))
        mbtn.addWidget(self._upb)
        self._downb = QPushButton(_t("dlg.down"))
        self._downb.setToolTip(_wrap_tip(_t("dlg.down.hint")))
        self._downb.clicked.connect(lambda: self._move(1))
        mbtn.addWidget(self._downb)
        left.addLayout(mbtn)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(200)
        body.addWidget(lw)

        right = QVBoxLayout()
        right.addWidget(QLabel(_t(self.NAME_KEY)))
        self._name = QLineEdit()
        self._name.setMaxLength(24)
        self._name.textChanged.connect(self._name_changed)
        right.addWidget(self._name)
        right.addWidget(QLabel(_t(self.BODY_KEY)))
        self._prompt = QPlainTextEdit()
        self._prompt.setPlaceholderText(_t(self.BODY_PLACEHOLDER_KEY))
        self._prompt.textChanged.connect(self._prompt_changed)
        right.addWidget(self._prompt, 1)
        rw = QWidget()
        rw.setLayout(right)
        body.addWidget(rw, 1)
        layout.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton(_t("dlg.close"))
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self._reload_list()
        if self._actions:
            row = (select_index if select_index is not None
                   and 0 <= select_index < len(self._actions) else 0)
            self._list.setCurrentRow(row)
        else:
            self._set_editor(False)

    def _reload_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        # Give every row an explicit height from the CURRENT font metrics –
        # otherwise the list's own row-height estimate can lag behind a live
        # font-size change and rows overlap at larger sizes.
        row_h = QFontMetrics(self._list.font()).height() + 16   # + padding
        for a in self._actions:
            item = QListWidgetItem(a.get("name") or "(ohne Namen)")
            item.setSizeHint(QSize(-1, row_h))
            self._list.addItem(item)
        self._list.blockSignals(False)

    def _set_editor(self, on: bool) -> None:
        self._name.setEnabled(on)
        self._prompt.setEnabled(on)
        self._delb.setEnabled(on)
        self._upb.setEnabled(on)
        self._downb.setEnabled(on)

    def _move(self, delta: int) -> None:
        cur = self._cur
        new = cur + delta
        if 0 <= cur < len(self._actions) and 0 <= new < len(self._actions):
            self._actions[cur], self._actions[new] = \
                self._actions[new], self._actions[cur]
            self._reload_list()
            self._list.setCurrentRow(new)     # keep the moved item selected

    def _select(self, row: int) -> None:
        self._cur = row
        if 0 <= row < len(self._actions):
            a = self._actions[row]
            self._name.blockSignals(True)
            self._prompt.blockSignals(True)
            self._name.setText(a.get("name", ""))
            self._prompt.setPlainText(a.get("prompt", ""))
            self._name.blockSignals(False)
            self._prompt.blockSignals(False)
            self._set_editor(True)
        else:
            self._set_editor(False)

    def _name_changed(self, text: str) -> None:
        if 0 <= self._cur < len(self._actions):
            self._actions[self._cur]["name"] = text
            item = self._list.item(self._cur)
            if item is not None:
                item.setText(text or "(ohne Namen)")

    def _prompt_changed(self) -> None:
        if 0 <= self._cur < len(self._actions):
            self._actions[self._cur]["prompt"] = self._prompt.toPlainText()

    def _add(self) -> None:
        self._actions.append({"name": _t("dlg.new_action"), "prompt": ""})
        self._reload_list()
        self._list.setCurrentRow(len(self._actions) - 1)
        self._name.setFocus()
        self._name.selectAll()

    def _remove(self) -> None:
        if 0 <= self._cur < len(self._actions):
            del self._actions[self._cur]
            self._reload_list()
            if self._actions:
                self._list.setCurrentRow(min(self._cur, len(self._actions) - 1))
            else:
                self._cur = -1
                self._name.clear()
                self._prompt.clear()
                self._set_editor(False)

    def done(self, r: int) -> None:      # save on any close (Schließen or X)
        self._on_save(self._actions)
        super().done(r)


class DictionaryDialog(QDialog):
    """Unified custom-dictionary editor (Dragon-style): each row shows the
    written form and its optional spoken form side by side plus an origin tag;
    a category box at the top filters by origin / spoken form; the first field
    is also the live search (so you see at once whether a word exists)."""

    def __init__(
        self,
        rows_provider: Callable[[str], list[tuple]],
        on_add: Callable[[str, str], None],
        on_edit: Callable[[str, str, str], None],
        on_remove: Callable[[str], None],
        categories: list[tuple[str, str]],
        on_export: Callable[[str], int] | None = None,
        on_import: Callable[[str], int] | None = None,
        on_learn: Callable[[], None] | None = None,
        on_clear_category: Callable[[str], int] | None = None,
        title: str = _t("dlg.vocab.title"),
        intro: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 580)
        self._rows_provider = rows_provider
        self._on_add = on_add
        self._on_edit = on_edit
        self._on_remove = on_remove
        self._on_export = on_export
        self._on_import = on_import
        self._on_clear_category = on_clear_category

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        if intro:
            lbl = QLabel(intro)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(_READABLE)
            layout.addWidget(lbl)

        # add / search row: written (also live-search) + spoken + Add
        add_row = QHBoxLayout()
        self._written = QLineEdit()
        self._written.setPlaceholderText(_t("dlg.vocab.search"))
        self._written.setClearButtonEnabled(True)
        self._written.textChanged.connect(self._on_written_changed)
        self._written.returnPressed.connect(self._add)
        add_row.addWidget(self._written, 2)
        self._spoken = QLineEdit()
        self._spoken.setPlaceholderText("wie gesprochen (optional)")
        self._spoken.returnPressed.connect(self._add)
        add_row.addWidget(self._spoken, 2)
        add_btn = QPushButton(_t("dlg.add"))
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        self._exists = QLabel("")
        self._exists.setStyleSheet(_READABLE)
        layout.addWidget(self._exists)

        # category filter + "learn from text"
        cat_row = QHBoxLayout()
        anzeige = QLabel("Anzeige:")
        anzeige.setStyleSheet(_READABLE)
        cat_row.addWidget(anzeige)
        self._category = QComboBox()
        for value, label in categories:
            self._category.addItem(label, value)
        self._category.currentIndexChanged.connect(lambda *_: self._reload())
        cat_row.addWidget(self._category, 1)
        if on_learn is not None:
            learn_btn = QPushButton(_t("dlg.learn.open"))
            learn_btn.clicked.connect(lambda: (on_learn(), self._reload()))
            cat_row.addWidget(learn_btn)
        layout.addLayout(cat_row)

        # An editable table: double-click a cell to change the written word or
        # its spoken form.  Much more readable than a list of inline fields.
        self._loading = False
        self._row_meta: list[tuple] = []
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            [_t("dlg.vocab.word"), "Gesprochen / erkannt als", "Herkunft", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed)
        # The system accent (e.g. orange) makes a selected row unreadable; use a
        # neutral translucent-grey selection that stays legible in light + dark
        # and keeps the normal text colour.
        self._table.setStyleSheet(
            "QTableWidget::item:selected {"
            " background: rgba(128,128,128,90); color: palette(text); }"
            "QTableWidget::item:selected:!active {"
            " background: rgba(128,128,128,70); color: palette(text); }")
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(3, 34)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

        footer = QHBoxLayout()
        if on_clear_category is not None:
            b = QPushButton(_t("dlg.clear_category"))
            b.setToolTip(_wrap_tip(_t("dlg.clear_category.hint.long")))
            b.clicked.connect(self._clear_category)
            footer.addWidget(b)
        if on_import is not None:
            b = QPushButton("Import …")
            b.setToolTip(_wrap_tip(_t("dlg.import.hint")))
            b.clicked.connect(self._import)
            footer.addWidget(b)
        if on_export is not None:
            b = QPushButton("Export …")
            b.setToolTip(_wrap_tip(_t("dlg.export.hint")))
            b.clicked.connect(self._export)
            footer.addWidget(b)
        footer.addStretch()
        close = QPushButton(_t("dlg.close"))
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self._reload()

    # -- population -----------------------------------------------------

    def _reload(self) -> None:
        self._loading = True
        self._table.setRowCount(0)
        self._row_meta = []
        query = self._written.text().strip().casefold()
        category = self._category.currentData() or "all"
        rows = [r for r in self._rows_provider(category)
                if not query or query in f"{r[3]} {r[2]}".casefold()]
        noedit = (Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        for kind, key, trigger, result, src in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._row_meta.append((kind, key))
            self._table.setItem(r, 0, QTableWidgetItem(result))    # editable
            spoken = QTableWidgetItem(trigger)
            if kind == "mem":     # the misheard text is history – not editable
                spoken.setFlags(noedit)
                spoken.setToolTip(_wrap_tip(_t("dlg.vocab.auto")))
            self._table.setItem(r, 1, spoken)
            origin = QTableWidgetItem(src)
            origin.setFlags(noedit)      # read-only; no grey foreground so it
            self._table.setItem(r, 2, origin)   # stays readable when selected
            btn = QPushButton("✕")
            btn.setFixedWidth(28)
            btn.setToolTip(_wrap_tip(_t("dlg.remove")))
            btn.clicked.connect(
                lambda _=False, kd=kind, k=key: self._remove(kd, k))
            self._table.setCellWidget(r, 3, btn)
        self._loading = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        if row >= len(self._row_meta):
            return
        kind, key = self._row_meta[row]
        w_item = self._table.item(row, 0)
        s_item = self._table.item(row, 1)
        result = w_item.text().strip() if w_item else ""
        trigger = s_item.text().strip() if s_item else ""
        if not result:                     # never allow an empty word
            self._loading = True
            if w_item:
                w_item.setText(key)
            self._loading = False
            return
        self._on_edit(kind, key, trigger, result)
        if kind == "dict":                 # the written form is the routing key
            self._row_meta[row] = ("dict", result)

    # -- actions --------------------------------------------------------

    def _on_written_changed(self, *_a) -> None:
        self._reload()
        typed = self._written.text().strip().casefold()
        if not typed:
            self._exists.setText("")
            return
        found = None
        for _kind, _key, trigger, result, _src in self._rows_provider("all"):
            if result.casefold() == typed:
                found = trigger
                break
        if found is not None:
            extra = f"  (gesprochen: {found})" if found else ""
            self._exists.setText("✓ existiert bereits" + extra)
        else:
            self._exists.setText(_t("dlg.vocab.new"))

    def _add(self) -> None:
        written = self._written.text().strip()
        if not written:
            return
        self._on_add(written, self._spoken.text().strip())
        self._written.clear()
        self._spoken.clear()
        self._reload()

    def _remove(self, kind: str, key: str) -> None:
        self._on_remove(kind, key)
        QTimer.singleShot(0, self._reload)

    def _clear_category(self) -> None:
        if self._on_clear_category is None:
            return
        cat = self._category.currentData() or "all"
        n = len(self._rows_provider(cat))
        if n == 0:
            return
        label = self._category.currentText()
        ok = QMessageBox.question(
            self, _t("dlg.clear_category"),
            _t("dlg.clear_category.confirm", name=label, n=str(n)))
        if ok == QMessageBox.StandardButton.Yes:
            self._on_clear_category(cat)
            self._written.clear()
            self._reload()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, _t("dlg.export"), "woerterbuch.txt",
            _t("dlg.filter.text"))
        if path and self._on_export is not None:
            try:
                self._on_export(path)
            except Exception:
                pass

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, _t("dlg.import"), "",
            _t("dlg.filter.import"))
        if path and self._on_import is not None:
            try:
                self._on_import(path)
            except Exception:
                pass
            self._written.clear()
            self._reload()
