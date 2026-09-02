"""Search across every settings page – including add-on modules.

The index is built by walking the actual widgets of the built pages instead of
from a hand-written table.  That way a module that ships its own settings page
(Diktieren, Trinkpause, anything installed later) is searchable without the
core knowing anything about it, and the index can never drift out of sync with
the UI.

Voice input: the microphone button asks the dictation module for one
transcription over the event bus (``dictation.capture_request`` →
``dictation.capture_result``).  The core never imports the module – if
Diktieren is not installed or not enabled, the button simply stays hidden.
Typing is the hardest input for many of the people this app is built for, so a
search box they cannot speak into would help the wrong audience.
"""
from __future__ import annotations

import uuid

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.gui import theme

_MAX_RESULTS = 12


class SearchEntry:
    """One findable setting: where it lives and which widget to reveal."""

    __slots__ = ("page_index", "page_name", "title", "widget", "haystack",
                 "groups")

    def __init__(self, page_index: int, page_name: str, title: str,
                 widget: QWidget, extra: str = "",
                 groups=None) -> None:
        self.page_index = page_index
        self.page_name = page_name
        self.title = title
        self.widget = widget
        self.groups = list(groups or [])
        # Group titles are searchable too, so "sticky position" finds the
        # Position row that sits inside the Sticky-Keys section.
        self.haystack = "\n".join(
            [title, page_name, " ".join(self.groups), extra]).casefold()

    def location(self) -> str:
        """Where the setting lives, e.g. "Tastatur > Sticky Keys".

        The page name alone is not enough: "Position" exists on three pages
        and more than once within one of them, so the enclosing card/section
        has to be part of the result line."""
        parts = [self.page_name] if self.page_name else []
        parts += self.groups
        # "Präzisionsmodus · Maus › Präzisionsmodus" says the same thing twice
        # – drop the innermost group when it IS the entry.
        if len(parts) > 1 and parts[-1] == self.title:
            parts.pop()
        return "  ›  ".join(parts)

    def matches(self, needle: str) -> bool:
        # Every whitespace-separated word must appear somewhere – lets
        # "maus zentrier" find "Cursor-Zentrierung" on the Maus page.
        return all(part in self.haystack for part in needle.split())


_CHEVRONS = "▸▾▶▼ "     # chevrons the section titles are prefixed with


def _group_titles(widget: QWidget, page: QWidget) -> list[str]:
    """Titles of the cards/sections the widget sits in, outermost first."""
    titles: list[str] = []
    node = widget.parentWidget()
    while node is not None and node is not page:
        title = ""
        checkbox = getattr(node, "_checkbox", None)     # CollapsibleSection
        button = getattr(node, "_btn", None)            # dictation _Collapsible
        if checkbox is not None and hasattr(node, "is_checked"):
            title = checkbox.text()
        elif button is not None and hasattr(node, "set_open"):
            title = button.text()
        elif node.objectName() == "card":
            for lbl in node.findChildren(QLabel):
                # card() puts its title label directly on the frame, so this
                # can never pick up a NESTED card's heading.
                if (lbl.parentWidget() is node
                        and lbl.objectName() in ("cardTitle",
                                                 "cardTitleDanger")):
                    title = lbl.text()
                    break
        title = " ".join(title.strip(_CHEVRONS).split())
        if title and title not in titles:
            titles.append(title)
        node = node.parentWidget()
    titles.reverse()
    return titles


_VALUE_WIDGETS = ("HotkeyEdit", "_KeyRecorder", "KeyListEdit")


def _inside_value_widget(widget: QWidget) -> bool:
    """True if the widget only DISPLAYS a value (a recorded hotkey etc.)."""
    node = widget.parentWidget()
    while node is not None:
        if node.__class__.__name__ in _VALUE_WIDGETS:
            return True
        node = node.parentWidget()
    return False


def _still_alive(widget: QWidget) -> bool:
    import shiboken6
    return bool(widget) and shiboken6.isValid(widget)


def _tooltip_of(widget: QWidget) -> str:
    """Tooltip text without the HTML wrapper wrap_tooltip() adds."""
    tip = widget.toolTip() or ""
    if "<" in tip:
        import re
        tip = re.sub(r"<[^>]+>", " ", tip)
    return tip


def build_index(stack: QWidget, page_names: dict[int, str]) -> list[SearchEntry]:
    """Collect every labelled control on every page of ``stack``."""
    entries: list[SearchEntry] = []
    seen: set[tuple[int, int]] = set()

    def add(idx: int, title: str, widget: QWidget, extra: str = "") -> None:
        title = " ".join(title.split())
        if not title or len(title) > 120:
            return
        key = (idx, id(widget))
        if key in seen:
            return
        seen.add(key)
        entries.append(SearchEntry(idx, page_names.get(idx, ""), title,
                                   widget, extra,
                                   _group_titles(widget, page)))

    for idx in range(stack.count()):
        page = stack.widget(idx)
        if page is None:
            continue
        # 1) Form rows: the label column names the setting, the field column is
        #    what we want to scroll to and highlight.
        for form in page.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                l_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                f_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if l_item is None or f_item is None:
                    continue
                lbl = l_item.widget()
                field = f_item.widget()
                if field is None:
                    # Rows whose field column is a LAYOUT (e.g. Schriftgröße =
                    # dropdown + apply button, Statusanzeige = spin + sync).
                    # Take its first real widget as the thing to jump to –
                    # otherwise these rows were simply not findable.
                    sub = f_item.layout()
                    for k in range(sub.count() if sub is not None else 0):
                        cand = sub.itemAt(k).widget()
                        if cand is not None:
                            field = cand
                            break
                if lbl is None or field is None:
                    continue
                text = lbl.text() if isinstance(lbl, QLabel) else ""
                extra = _tooltip_of(field)
                if not text:
                    # label_with_hint() wraps text + ⓘ icon in a container
                    inner = lbl.findChildren(QLabel)
                    text = inner[0].text() if inner else ""
                    # The ⓘ tooltip carries what the caption no longer spells
                    # out ("Auslösetaste" + "…schaltet den Makromodus ein"), so
                    # it has to be searchable – otherwise shortening a caption
                    # silently makes the setting harder to find.
                    extra = " ".join([extra] + [_tooltip_of(w) for w in inner[1:]])
                if text:
                    add(idx, text, field, extra)
        # 2) Checkboxes carry their own label (and are often standalone rows).
        for cb in page.findChildren(QCheckBox):
            if cb.text():
                add(idx, cb.text(), cb, _tooltip_of(cb))
        # 3) Card / section headings, so "Notfall" finds the emergency card.
        for lbl in page.findChildren(QLabel):
            if lbl.objectName() in ("cardTitle", "cardTitleDanger") and lbl.text():
                add(idx, lbl.text(), lbl)
        # 4) Text buttons that ARE the setting (e.g. the Design / Kontrast
        #    choice, which lives in a layout and therefore has no form label).
        for btn in page.findChildren(QPushButton):
            label = " ".join(btn.text().split())
            # Skip pure glyph buttons (✕, ▲, ▼ …) – their text says nothing.
            if len(label) < 3:
                continue
            # Skip buttons that show a VALUE rather than name a setting: a
            # hotkey recorder's face is "Strg + M" / "— nicht belegt —", which
            # is not something anyone searches for, and the row it sits in is
            # already indexed under its real label.
            if _inside_value_widget(btn):
                continue
            add(idx, label, btn, _tooltip_of(btn))
        # 5) One entry for the page itself, carrying its intro/description text.
        #    Lets a plain-language word from the description find the page even
        #    when no single control is called that ("trinken" → Trinkpause).
        blurb = " ".join(
            lbl.text() for lbl in page.findChildren(QLabel)
            if lbl.wordWrap() and lbl.text() and len(lbl.text()) > 30
        )[:600]
        name = page_names.get(idx, "")
        if name:
            add(idx, name, page, blurb)
    return entries


def reveal(entry: SearchEntry, stack: QWidget, goto_page) -> None:
    """Switch to the entry's page, open anything it is folded inside, scroll to
    it and flash it briefly so the eye can find it."""
    if not _still_alive(entry.widget):
        return
    goto_page(entry.page_index)

    # Open every collapsed ancestor (both section flavours in the app).
    node = entry.widget
    chain = []
    while node is not None:
        chain.append(node)
        node = node.parentWidget()
    for anc in reversed(chain):
        setter = getattr(anc, "set_checked", None)     # CollapsibleSection
        if callable(setter) and hasattr(anc, "is_checked"):
            if not anc.is_checked():
                setter(True)
            continue
        opener = getattr(anc, "set_open", None)        # dictation _Collapsible
        if callable(opener):
            opener(True)

    def do_scroll() -> None:
        if not _still_alive(entry.widget):
            return
        area = entry.widget.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is not None:
            area.ensureWidgetVisible(entry.widget, 40, 60)
        _flash(entry.widget)

    # Deferred: the page/section has to lay out before scrolling makes sense.
    QTimer.singleShot(0, do_scroll)


def _flash(widget: QWidget) -> None:
    """Briefly outline the found widget (see theme QSS [searchHit])."""
    if not _still_alive(widget):
        return
    widget.setProperty("searchHit", True)
    widget.style().unpolish(widget)
    widget.style().polish(widget)

    def clear() -> None:
        if not _still_alive(widget):
            return
        widget.setProperty("searchHit", False)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    QTimer.singleShot(2500, clear)


class SettingsSearchBar(QWidget):
    """Search field + microphone + result list, sitting above the pages."""

    # The dictation module answers from its transcription WORKER thread, so the
    # bus callback must not touch widgets directly – it hops onto the GUI
    # thread through this signal first (the same bridge pattern the dictation
    # module uses internally).  Without it the text landed in the field only
    # by luck.
    _captured = Signal(str, str)        # token, text

    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._entries: list[SearchEntry] = []
        self._capture_token = ""
        self._overlay_host: QWidget | None = None
        self._results_h = 0
        self._captured.connect(self._apply_capture)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 14, 24, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._edit = QLineEdit()
        self._edit.setObjectName("settingsSearch")
        self._edit.setPlaceholderText(tr("settings.search.placeholder"))
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self._on_text)
        self._edit.returnPressed.connect(self._activate_first)
        # Esc cancels, ↑/↓ walk the hit list without leaving the text field –
        # both are handled in eventFilter() below.
        self._edit.installEventFilter(self)
        row.addWidget(self._edit, 1)

        self._mic_btn = QPushButton("🎤")
        self._mic_btn.setProperty("iconBtn", True)
        self._mic_btn.setToolTip(tr("settings.search.mic"))
        self._mic_btn.setAccessibleName(tr("settings.search.mic"))
        self._mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_btn.clicked.connect(self._on_mic)
        self._mic_btn.setVisible(False)     # only when Diktieren can serve it
        row.addWidget(self._mic_btn)
        outer.addLayout(row)

        # Two aligned columns (setting | where it lives) rather than one run-on
        # line: with the location appended to the text, the eye had to re-find
        # the "·" on every row to tell name from place.
        # Parented to the bar (NOT parentless): a widget with no parent that
        # is made visible becomes a real top-level window.  set_overlay_host()
        # moves it onto the content area right after construction.
        self._results = QTreeWidget(self)
        self._results.setObjectName("searchResults")
        self._results.setColumnCount(2)
        self._results.setHeaderHidden(True)
        self._results.setRootIsDecorated(False)
        self._results.setUniformRowHeights(True)
        self._results.setAllColumnsShowFocus(True)
        self._results.header().setStretchLastSection(True)
        theme.style_item_view(self._results, "QTreeWidget")
        # As a floating overlay the list needs its own visible edge – without
        # one it melts into the page it is covering.
        self._results.setStyleSheet(
            self._results.styleSheet()
            + f"\nQTreeWidget#searchResults {{ border: 1px solid "
              f"{theme.border_color()}; border-radius: 8px; }}")
        self._results.itemActivated.connect(self._on_pick)
        self._results.itemClicked.connect(self._on_pick)
        self._results.setVisible(False)
        # Deliberately NOT added to the layout – see set_overlay_host().

        bus.subscribe("dictation.capture_result", self._on_capture_result)
        bus.subscribe("module.started", self._on_module_state)
        bus.subscribe("module.stopped", self._on_module_state)
        self.destroyed.connect(self._unsubscribe)
        self._refresh_mic_visibility()

    # -- result overlay ---------------------------------------------------

    def set_overlay_host(self, host: QWidget) -> None:
        """Float the result list ON TOP of ``host`` instead of above it.

        As an ordinary layout row the list pushed the whole settings page down
        the moment a query matched, and pulled it back up on every keystroke
        that changed the number of hits – the content the user was reading
        jumped around while they typed.  As an overlay nothing below it moves.
        """
        self._overlay_host = host
        self._results.setParent(host)     # implicitly hides it
        host.installEventFilter(self)     # keep it positioned on resize

    def _position_results(self) -> None:
        """Put the list directly under the search row, spanning its width."""
        host = self._overlay_host
        if host is None or self._results_h <= 0:
            return
        left, _, right, _ = self.layout().getContentsMargins()
        top_left = self.mapTo(host, QPoint(left, self.height()))
        self._results.setGeometry(top_left.x(), top_left.y() + 4,
                                  max(self.width() - left - right, 0),
                                  self._results_h)
        self._results.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_results()

    # -- index ------------------------------------------------------------

    def set_entries(self, entries: list[SearchEntry]) -> None:
        self._entries = entries

    def clear_query(self) -> None:
        self._edit.clear()

    def focus_input(self) -> None:
        self._edit.setFocus()
        self._edit.selectAll()

    # -- searching --------------------------------------------------------

    def _on_text(self, text: str) -> None:
        needle = text.strip().casefold()
        self._results.clear()
        if len(needle) < 2:
            self._results.setVisible(False)
            return
        hits = [e for e in self._entries
                if _still_alive(e.widget) and e.matches(needle)][:_MAX_RESULTS]
        for e in hits:
            item = QTreeWidgetItem([e.title, e.location()])
            item.setData(0, Qt.ItemDataRole.UserRole, e)
            item.setForeground(1, QBrush(QColor(theme.hint_color())))
            self._results.addTopLevelItem(item)
        if hits:
            self._results.setCurrentItem(self._results.topLevelItem(0))
            self._results.resizeColumnToContents(0)
        else:
            self._results.addTopLevelItem(
                QTreeWidgetItem([tr("settings.search.none"), ""]))
        # Height follows the number of hits so the list never covers more of
        # the page than it has to.  Measured from a real row rather than
        # guessed from the font, otherwise the box keeps a block of empty
        # space under the last hit.
        count = self._results.topLevelItemCount()
        rows = min(max(count, 1), 6)
        row_h = self._results.sizeHintForRow(0)
        if row_h <= 0:
            from withease.gui.ui_utils import em
            row_h = em(1.4)
        self._results_h = (rows * row_h + 2 * self._results.frameWidth() + 4)
        self._results.setFixedHeight(self._results_h)
        self._position_results()
        self._results.setVisible(True)
        self._results.raise_()

    def eventFilter(self, obj: object, event: object) -> bool:  # noqa: N802
        """Esc clears the search; ↑/↓ move through the hits from the field."""
        if obj is self._overlay_host and event.type() == QEvent.Type.Resize:
            self._position_results()
            return False
        if obj is self._edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                # Give up on the search entirely: empty field, list closed,
                # and a running voice capture stopped as well.
                self.cancel()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) \
                    and self._results.isVisible()                     and self._results.topLevelItemCount():
                step = 1 if key == Qt.Key.Key_Down else -1
                total = self._results.topLevelItemCount()
                cur = self._results.indexOfTopLevelItem(
                    self._results.currentItem())
                self._results.setCurrentItem(
                    self._results.topLevelItem((max(cur, 0) + step) % total))
                return True
        return super().eventFilter(obj, event)

    def cancel(self) -> None:
        """Reset the search box to its resting state (Esc)."""
        if self._capture_token:
            bus.publish("dictation.capture_stop", token=self._capture_token)
            self._capture_token = ""
            self._set_listening(False)
        self._edit.clear()
        self._results.clear()
        self._results.setVisible(False)

    def _activate_first(self) -> None:
        if self._results.isVisible() and self._results.topLevelItemCount():
            item = self._results.currentItem() or self._results.topLevelItem(0)
            self._on_pick(item)

    def _on_pick(self, item, _column: int = 0) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(entry, SearchEntry):
            return
        self._results.setVisible(False)
        self._edit.clear()
        reveal(entry, self._window._stack, self._window._goto_page)

    # -- voice ------------------------------------------------------------

    def _refresh_mic_visibility(self) -> None:
        """Show the microphone only while a dictation module is running."""
        ok = False
        try:
            for module in self._window._app.get_modules():
                if module.MODULE_ID == "dictation" and module.enabled:
                    ok = True
                    break
        except Exception:
            ok = False
        self._mic_btn.setVisible(ok)

    def _on_module_state(self, module_id: str = "", **_: object) -> None:
        if module_id == "dictation":
            QTimer.singleShot(0, self._refresh_mic_visibility)

    def _set_listening(self, on: bool) -> None:
        """Reflect the recording state on the button and the placeholder."""
        self._mic_btn.setText("⏹" if on else "🎤")
        key = "settings.search.mic.stop" if on else "settings.search.mic"
        self._mic_btn.setToolTip(tr(key))
        self._mic_btn.setAccessibleName(tr(key))
        self._edit.setPlaceholderText(tr(
            "settings.search.listening" if on else "settings.search.placeholder"))

    def _on_mic(self) -> None:
        # Second click = stop.  The button starts the recording, so it must be
        # able to end it too – needing a separate hotkey to finish what a
        # mouse click began defeats the point of the button.
        if self._capture_token:
            bus.publish("dictation.capture_stop", token=self._capture_token)
            return

        token = self._capture_token = uuid.uuid4().hex
        self._set_listening(True)
        # Focus the field up front so the caret is already here; the result is
        # delivered over the bus either way, but this also makes the spoken
        # text land right if the user types instead.
        self._edit.setFocus()
        bus.publish("dictation.capture_request", token=token)

        # Safety net: if no answer ever arrives (module stopped mid-request,
        # microphone failure …) the field must not stay stuck on "Speak now".
        def give_up() -> None:
            if self._capture_token == token:
                self._capture_token = ""
                self._set_listening(False)

        QTimer.singleShot(120_000, give_up)

    def _on_capture_result(self, token: str = "", text: str = "",
                           **_: object) -> None:
        # Called on the dictation worker thread – hand over to the GUI thread.
        self._captured.emit(token or "", text or "")

    def _apply_capture(self, token: str, text: str) -> None:
        if token != self._capture_token:
            return                      # not our request
        self._capture_token = ""
        self._set_listening(False)
        if text:
            self._edit.setText(text)
            self._edit.setFocus()

    def _unsubscribe(self) -> None:
        for topic, cb in (("dictation.capture_result", self._on_capture_result),
                          ("module.started", self._on_module_state),
                          ("module.stopped", self._on_module_state)):
            try:
                bus.unsubscribe(topic, cb)
            except Exception:
                pass
