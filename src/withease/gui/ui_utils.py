"""Small UI helpers shared across settings pages and dialogs."""
from __future__ import annotations

import re

from withease.core.i18n import tr
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)


def card(title: str = "", icon: str = "",
         danger: bool = False) -> tuple[QFrame, QVBoxLayout]:
    """A styled content card (see theme.app_stylesheet, ``QFrame#card``).

    Returns ``(card_widget, body_layout)`` – add the card to the page and put
    the card's content into ``body_layout``.  An optional title row (small icon
    + bold heading) is added automatically when ``title`` is given.  With
    ``danger=True`` the title/icon are shown in the danger colour (a highlight
    that is not permanently alarming).  Styling is fully central in theme.py;
    this only builds the structure.
    """
    frame = QFrame()
    frame.setObjectName("card")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(0, 0, 0, 0)   # padding comes from the QSS
    outer.setSpacing(12)

    if title:
        header = QHBoxLayout()
        header.setSpacing(8)
        title_obj = "cardTitleDanger" if danger else "cardTitle"
        if icon:
            icon_lbl = QLabel(icon)
            # Fixed size on purpose (see theme.py) – icons stay put while the
            # title text next to them still follows the font-size setting.
            icon_lbl.setObjectName("cardIconDanger" if danger else "cardIcon")
            header.addWidget(icon_lbl)
        title_lbl = QLabel(title)
        title_lbl.setObjectName(title_obj)
        header.addWidget(title_lbl)
        header.addStretch()
        outer.addLayout(header)

    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(10)
    outer.addLayout(body)
    return frame, body


class WrappingLabel(QLabel):
    """A word-wrapped QLabel (typically combined with ``setMaximumWidth()``
    to keep a text column at a readable width) that reliably reserves
    enough height for its wrapped content.

    Plain QLabel + ``setWordWrap(True)`` + ``setMaximumWidth()`` can end up
    with a stale, too-short cached ``heightForWidth()`` once nested inside a
    few layout levels (e.g. QScrollArea > page > card) – the layout then
    gives it only its first line's height, and the label's later lines
    silently overlap the next widget instead of pushing it down.  Recomputed
    on every resize so it also stays correct if the window is resized."""

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.setMinimumHeight(self.heightForWidth(self.width()))


def label_with_hint(text: str, tooltip: str) -> QWidget:
    """A QFormLayout row-label made of plain text plus a HintIcon carrying the
    longer explanation as a tooltip – use as ``form.addRow(label_with_hint(
    "Kurzname", "Längere Erklärung"), field_widget)`` instead of a bare hint
    QLabel under the field, so the explanation stays out of the way until the
    user hovers the icon.  Local import avoids a hard import cycle with
    ``widgets/hint_icon.py`` (same reasoning as ``card()``'s own imports)."""
    from withease.gui.widgets.hint_icon import HintIcon

    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(QLabel(text))
    layout.addWidget(HintIcon(tooltip))
    layout.addStretch(1)
    return row


def setting_note(text: str, checkbox: bool = False) -> QLabel:
    """A permanently visible explanation of a setting.

    The counterpart to ``label_with_hint``: a tooltip asks the user to hold a
    pointer steady on a small icon, which is exactly the movement this program
    exists to make unnecessary.  So anything needed to DECIDE stays on screen,
    and only the deepening detail goes behind the ⓘ.

    Deliberately rare – six settings in the whole program.  Use it when at
    least two of these are true: data leaves the PC, something is downloaded
    or deleted, the setting is met once while setting up, a wrong choice makes
    the program look broken, or the caption alone does not say what happens.

    Formatted like the tooltips (wrap_tooltip): one paragraph per line, and the
    lead-in before the first colon in bold.  A wall of two unbroken lines is
    what made the first version hard to read.

    WHERE IT GOES – one rule, everywhere:

    * An explanation that belongs to ONE control lives in the control column,
      directly under it, so the two read as one block::

          form.addRow(caption, control)
          form.addRow("", setting_note(tr("...")))

    * An explanation that applies to the whole card spans the full width::

          form.addRow(setting_note(tr("...")))

      A note under a single control but spanning the card looks like it
      belongs to the card, which is exactly the confusion this rule removes.

    ``checkbox=True`` additionally indents by the width of a checkbox
    indicator, so the text lines up with the CAPTION of the checkbox above it
    rather than with the little box.
    """
    from withease.gui import theme

    label = WrappingLabel(_note_html(text))
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setStyleSheet(theme.hint_style())
    # No padding of its own: the note has to sit right under its control, and
    # a label's default margin is enough to make it look detached again.
    label.setMargin(0)
    label.setContentsMargins(0, 0, 0, 0)
    # Claim the width the card actually has.  Without this the note is only as
    # wide as the WIDEST CONTROL in the form (that is what sizes the field
    # column), so a two-sentence explanation wrapped into five stubby lines
    # beside half a card of empty space.
    label.setSizePolicy(QSizePolicy.Policy.MinimumExpanding,
                        QSizePolicy.Policy.Preferred)
    # Top-aligned: centred inside its row the text drifted away from the
    # control it explains, which is the whole thing this is meant to fix.
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    if checkbox:
        label.setIndent(em(1.4))
    return label


def _note_html(text: str) -> str:
    """Paragraphs and bold lead-ins, the same convention as the tooltips."""
    import html as _html

    parts = [p.strip() for p in str(text).split("\n") if p.strip()]
    out = []
    for part in parts:
        head, sep, rest = part.partition(":")
        if sep and len(head) <= 30 and len(parts) > 1:
            out.append(f"<b>{_html.escape(head)}:</b>"
                       f"{_html.escape(rest)}")
        else:
            out.append(_html.escape(part))
    return "<br><br>".join(out)


def checkbox_with_hint(checkbox: QCheckBox, tooltip: str) -> QWidget:
    """A checkbox row with the ⓘ icon right after its caption.

    A checkbox carries its own text, so it has no label column to hang the
    icon on – yet an explanation reachable only by hovering the checkbox
    itself is invisible: nothing on screen says it exists.  Wrapping the pair
    keeps the promise that an explanation is ALWAYS marked by a ⓘ sitting
    directly after the name it explains.
    """
    from withease.gui.widgets.hint_icon import HintIcon

    checkbox.setToolTip(wrap_tooltip(tooltip))
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(checkbox)
    layout.addWidget(HintIcon(tooltip))
    layout.addStretch(1)
    return row


def mark_danger(button) -> None:
    """Tint a button that DELETES something (see theme's QPushButton[danger]).

    One helper rather than a stylesheet per button, so every destructive
    action in the app looks the same and none is forgotten.  Safe to call on
    an already-styled button; it only sets the property Qt styles on."""
    button.setProperty("danger", True)
    button.style().unpolish(button)
    button.style().polish(button)
    # Deferred: at this point the button is usually not in its layout yet.
    QTimer.singleShot(0, lambda: _separate_from_neighbour(button))
    return button


def _separate_from_neighbour(button) -> None:
    """Put a gap between a delete button and the harmless button beside it.

    "Bearbeiten | Löschen", "Umbenennen | Löschen", "Neu | Entfernen" – with
    normal layout spacing these sit a few pixels apart, and a hand that
    overshoots by a few pixels hits the wrong one.  Missing the button is
    recoverable; hitting the one next to it is not, so the two must not be
    neighbours.

    Only where it actually helps: a preceding widget that stretches (a label
    filling the row) has already pushed the button far away, and a spacer or
    a stretch means there is no neighbour to be confused with.
    """
    import shiboken6
    if not shiboken6.isValid(button) or button.property("dangerGap"):
        return
    parent = button.parentWidget()
    layout = _layout_containing(parent.layout() if parent else None, button)
    if layout is None:
        return
    index = layout.indexOf(button)
    if index < 1:
        return
    before = layout.itemAt(index - 1)
    neighbour = before.widget() if before is not None else None
    if neighbour is None:                      # a stretch or a spacer
        return
    if neighbour.sizePolicy().horizontalStretch():
        return                                 # already pushed apart
    gap = max(12, em(0.8))
    if isinstance(layout, QBoxLayout):
        if layout.stretch(index - 1):
            return
        layout.insertSpacing(index, gap)
    elif hasattr(layout, "insertItem"):        # our FlowLayout
        layout.insertItem(index, QSpacerItem(
            gap, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))
    else:
        return
    button.setProperty("dangerGap", True)


def inside_click_target(widget) -> bool:
    """True if an ancestor already provides the click area for ``widget``.

    A card header toggles its checkbox from anywhere in the header row, so the
    small box inside it does not have to be a target of its own.  Containers
    say so by setting the ``clickTarget`` property (collapsible_section.py).
    """
    parent = widget.parentWidget()
    while parent is not None:
        if parent.property("clickTarget"):
            return True
        parent = parent.parentWidget()
    return False


def _layout_containing(layout, widget):
    """The innermost layout that holds ``widget`` directly, or None."""
    if layout is None:
        return None
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        child = layout.itemAt(i)
        sub = child.layout() if child is not None else None
        found = _layout_containing(sub, widget)
        if found is not None:
            return found
    return None


def set_option_hint(combo, index: int, text: str) -> None:
    """Explain ONE entry of a dropdown, shown when it is hovered in the open
    list.

    The app's two places for an explanation, and only these two, so the user
    always knows where to look: the ⓘ icon right after a setting's NAME
    (``label_with_hint``), and the entry itself for a CHOICE within that
    setting.  Keeping the long form out of the entry text also stops one
    verbose option from setting the width of the whole dropdown.
    """
    combo.setItemData(index, wrap_tooltip(text), Qt.ItemDataRole.ToolTipRole)


def ensure_card_visible(widget: QWidget, margin: int = 12) -> None:
    """Scroll ``widget``'s page so the WHOLE widget is on screen.

    Called after a card unfolds: the new content usually opens below the fold,
    so the user had to scroll manually to see what their own click just
    revealed.  ``QScrollArea.ensureWidgetVisible`` is not enough here – it
    guarantees a point, not a whole widget, and on a card taller than the
    viewport it happily scrolls the heading out of view.  This keeps the TOP
    anchored whenever the card cannot fit completely: the heading is what tells
    you which card you are looking at.

    Deferred by one event-loop turn on purpose – right after the toggle the
    card still has its old, collapsed height.
    """
    def run() -> None:
        import shiboken6
        if not shiboken6.isValid(widget) or not widget.isVisible():
            return
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QScrollArea
        area = widget.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is None or area.widget() is None:
            return
        top = widget.mapTo(area.widget(), QPoint(0, 0)).y() - margin
        bottom = top + widget.height() + 2 * margin
        bar = area.verticalScrollBar()
        view_h = area.viewport().height()
        if bottom > bar.value() + view_h:
            bar.setValue(int(min(bottom - view_h, top)))
        elif top < bar.value():
            bar.setValue(int(top))

    QTimer.singleShot(0, run)


# Reading width, not just a safety cap.  Qt only word-wraps a tooltip when it
# is rich text, so a long plain-text explanation renders as one endless line;
# but wrapping it too narrow is just as bad – a three-word column is harder to
# read than a wide one.  ~62 characters per line is the classic readable
# measure, and this still stays comfortably inside MainWindow's 800px minimum
# so a tip can never be wider than the window it explains.
_TOOLTIP_LINE_CHARS = 62
_TOOLTIP_MIN_WIDTH = 420
_TOOLTIP_MAX_WIDTH = 560
# Floor for a very short tip, so a three-word hint is not squeezed into a
# one-word-per-line sliver either.
_TOOLTIP_SHORT_WIDTH = 160

# "Halten: …" / "Cloud-Dienst: …" – a short lead-in naming the thing being
# explained.  Capped in length so a normal sentence that happens to contain a
# colon ("Zum Diagnostizieren: …") is not mistaken for one.
_LEAD_IN = re.compile(r"^([^:]{2,28}):\s+(.*)$", re.DOTALL)


def _spacer_pt() -> int:
    """Point size of the blank line between tooltip paragraphs."""
    app = QApplication.instance()
    base = app.font().pointSize() if app else 9
    return max(3, round(base * 0.45))


def _tooltip_width(paragraphs: list[str]) -> int:
    """Render width in px: the text's own width, capped at a reading measure.

    A short tip stays short instead of being stretched to a fixed box, and a
    long one wraps at a comfortable line length rather than running across the
    screen.  Both bounds scale with the font-size setting.
    """
    app = QApplication.instance()
    if app is None:
        return _TOOLTIP_MIN_WIDTH
    fm = QFontMetrics(app.font())
    # "0" is a good stand-in for the average character width of a UI font.
    target = max(_TOOLTIP_MIN_WIDTH,
                 min(_TOOLTIP_MAX_WIDTH,
                     fm.horizontalAdvance("0") * _TOOLTIP_LINE_CHARS))
    # +16px covers the bold lead-in, which measures wider than the plain text.
    natural = max((fm.horizontalAdvance(p) for p in paragraphs), default=0) + 16
    return max(_TOOLTIP_SHORT_WIDTH, min(target, natural))


def wrap_tooltip(text: str) -> str:
    """Render an explanation as a readable tooltip.

    Wrap the result in ``setToolTip()`` for any tooltip long enough to need it
    (``HintIcon`` and ``set_option_hint`` do this already; use directly for a
    plain ``setToolTip()`` call on a longer text).

    Two pieces of formatting, both driven by the text itself so the wording
    stays the single source:

    * a blank line or a newline starts a NEW PARAGRAPH – used where one tip
      explains several alternatives ("Halten …" / "Umschalten …"), which as one
      run-on block was genuinely hard to tell apart;
    * a paragraph that starts with a short ``Lead-in:`` gets that lead-in in
      bold, so the eye can jump straight to the option it is looking for.
    """
    from html import escape
    parts = [p.strip() for p in re.split(r"\n\s*", text.strip()) if p.strip()]
    width = _tooltip_width(parts)
    # Bold lead-ins ONLY when the tip actually compares alternatives (more than
    # one paragraph).  In a single paragraph a colon is usually just part of a
    # sentence – "Wenn gesetzt: …", "Nach dem Klick: …" – and emphasising that
    # half looks like a mistake.  This way the bold text always means the same
    # thing: "here is one of the options".
    compare = len(parts) > 1
    blocks = []
    for i, part in enumerate(parts):
        m = _LEAD_IN.match(part) if compare else None
        if m:
            body = f"<b>{escape(m.group(1))}</b>&nbsp; {escape(m.group(2))}"
        else:
            body = escape(part)
        # Qt's rich text ignores margins on <p>, so the gap between paragraphs
        # is made with an explicit spacer line instead – sized from the app
        # font, so it keeps its proportion at every font-size setting.
        if i:
            blocks.append(f'<div style="font-size:{_spacer_pt()}pt;">'
                          f'&nbsp;</div>')
        blocks.append(f'<div style="line-height:135%;">{body}</div>')
    # A single-cell TABLE, not a styled div: Qt's tooltip label word-wraps rich
    # text, and under word wrap a div's max-width/width is ignored while its
    # sizeHint collapses to a very narrow column – measured 146px for a text
    # asking for 400px.  That is exactly why some tips looked far too narrow.
    # A table width is honoured to the pixel.
    return (f'<table width="{width}" cellspacing="0" cellpadding="0">'
            f'<tr><td>{"".join(blocks)}</td></tr></table>')


def em(units: float) -> int:
    """Pixels for ``units`` line heights of the CURRENT application font.

    Use this instead of hard-coded pixel sizes wherever a widget dimension
    should grow with the user's font-size setting (accessibility): the value
    is re-evaluated on every rebuild, so the layout scales with the font.
    """
    app = QApplication.instance()
    line = QFontMetrics(app.font()).height() if app else 16
    return round(line * units)


def combo_needed_width(combo: QComboBox) -> int:
    """Width a combo box really needs for its longest entry.

    ``QComboBox.sizeHint()`` measures its own text area, but the stylesheet
    adds ``padding-right`` to keep the drop-down arrow off the text – and that
    padding is not reflected back into the hint.  The result was a box whose
    longest entry fitted by 4px at every font size, i.e. clipped as soon as
    anything rounded the other way (this is why the language box showed
    "Deutsc…").  Measure the text ourselves, add the icon, and compare against
    the space the style actually leaves for it.
    """
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

    hint = combo.sizeHint().width()
    if combo.count() == 0:
        return hint
    fm = QFontMetrics(combo.font())
    text = max(fm.horizontalAdvance(combo.itemText(i))
               for i in range(combo.count()))
    icon = 0
    if any(not combo.itemIcon(i).isNull() for i in range(combo.count())):
        icon = combo.iconSize().width() + 6      # icon + its gap to the text
    opt = QStyleOptionComboBox()
    combo.initStyleOption(opt)
    field = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, opt,
        QStyle.SubControl.SC_ComboBoxEditField, combo).width()
    # A few px of breathing room on top: sitting exactly on the boundary is
    # what made this fail in the first place.
    deficit = (text + icon + 8) - field
    return hint + deficit if deficit > 0 else hint


def fix_combo_widths(root: QWidget) -> None:
    """Make sure no dropdown clips its longest entry.

    Must run when the widgets are POLISHED and laid out: at build time the
    style still reports the unpadded text area, so the correction comes out as
    zero.  Cheap enough to repeat – it only ever widens, never shrinks, so
    calling it again when a page is shown is safe.
    """
    import shiboken6
    if not shiboken6.isValid(root):
        return
    for combo in root.findChildren(QComboBox):
        need = combo_needed_width(combo)
        if need > combo.minimumWidth():
            combo.setMinimumWidth(need)


def align_form_labels(root: QWidget) -> None:
    """Give every card on ONE page the same caption column width.

    Each card carries its own QFormLayout, and each sized its caption column
    to its own longest caption – so the controls started a few pixels further
    right on one card than on the next.  Small differences, but the eye reads
    a broken vertical line as untidy, which is exactly what it is.

    Per page, never across pages: aligning to the longest caption in the whole
    program would push every control far right on the pages that don't have
    it.  Only ever widens (like fix_combo_widths), so repeating it on each
    visit is safe, and the caption's own sizeHint is what we measure – it does
    not grow with the minimum we set, so this cannot creep.
    """
    import shiboken6
    if not shiboken6.isValid(root):
        return
    labels: list[QWidget] = []
    for form in root.findChildren(QFormLayout):
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            w = item.widget() if item is not None else None
            # addRow("", field) creates no label widget at all; a row whose
            # caption is empty must not take part (it would only indent a
            # self-captioned checkbox for nothing).
            if w is not None and (not isinstance(w, QLabel) or w.text()):
                labels.append(w)
    if not labels:
        return
    # Capped for the same reason compact_fields() caps the field width: one
    # unusually long caption – a translation, most likely – must not push the
    # controls of every card halfway across the page.  A caption beyond the
    # cap simply keeps its own width, and only its own row steps out of line.
    widest = min(max(w.sizeHint().width() for w in labels), em(15))
    for w in labels:
        if w.sizeHint().width() <= widest:
            w.setMinimumWidth(widest)


def display_key_name(name: str) -> str:
    """Localised display name for a special key ('home' → 'Pos1' in German).

    Falls back to a generic rendering (F-keys, unknown keys) when no
    translation exists.
    """
    translated = tr(f"key.{name}")
    if translated != f"key.{name}":
        return translated
    # Modifier keys incl. pynput variants: ctrl/ctrl_l/ctrl_r → "Strg",
    # cmd (pynput's name for the Windows key) → "Win".
    stem = name[:-2] if name.endswith(("_l", "_r")) else name
    stem = {"cmd": "win", "alt_gr": "altgr"}.get(stem, stem)
    if stem in ("ctrl", "shift", "alt", "altgr", "win"):
        return tr(f"key.mod.{stem}")
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return name.replace("_", " ").capitalize()


def compact_fields(root: QWidget) -> None:
    """Stop input fields from stretching to the full form width.

    Combo boxes size themselves to their longest entry, spin boxes and
    buttons to their content – so the dropdown arrow / button edge is right
    next to the text instead of far off at the window border.  Sliders and
    line edits keep their full width (they benefit from the space), and
    widgets with an explicitly fixed width are left untouched.
    """
    for combo in root.findChildren(QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    for spin in root.findChildren(QAbstractSpinBox):
        spin.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    for btn in root.findChildren(QPushButton):
        if btn.minimumWidth() == btn.maximumWidth():
            continue  # explicitly fixed size (e.g. ▲▼, ✕, colour button)
        btn.setSizePolicy(QSizePolicy.Policy.Maximum,
                          btn.sizePolicy().verticalPolicy())

    # Every checkbox is its own click target and must meet the floor – a 20px
    # strip 8px from the next one means a vertical overshoot silently toggles
    # the neighbouring setting.  Wherever a container already IS the target (a
    # card header, a table cell), it says so and the box is left alone, since
    # stretching it there would only push its indicator out of place.
    from withease.gui import theme as _theme
    target = _theme.target_px()
    for box in root.findChildren(QCheckBox):
        if not inside_click_target(box):
            box.setMinimumHeight(target)

    # Second pass: within EACH form, give every single-widget field (a bare
    # combo box or spin box – rows whose field is a button/layout/container
    # are left alone since item.widget() won't be one of these two types)
    # the same width as the widest one in that form.  Otherwise a short
    # field (e.g. "12 pt") sits next to a much longer one (e.g. a theme
    # name) in the same card, looking visually inconsistent.
    for form in root.findChildren(QFormLayout):
        # Qt's default puts the row label at the TOP of the field column.  With
        # controls now a full click-target tall, the caption visibly floated
        # above its own dropdown on every page but one (the only form that
        # happened to set this itself).  Doing it here fixes the core AND every
        # add-on module's page, since compact_fields() runs on every build.
        # Pin each label to exactly ONE control row, anchored at the top.
        # setLabelAlignment alone is not enough (QFormLayout lines a label up
        # with the first LINE of its field, and then stretches it), which left
        # a 16px caption drawn 78px tall beside a tall key list – its text
        # ended up well below the first row of keys.  One row tall + top
        # anchored gives both cases the right answer at once: level with a
        # normal control, and level with the FIRST row of a field that grows
        # downwards.
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignTop)
        from withease.gui import theme as _theme
        row_h = _theme.target_px()
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            lbl = item.widget() if item is not None else None
            # An EMPTY label column (a row that is just a self-captioned
            # checkbox) must not force a full control row of height – that is
            # pure wasted space on a list of five such rows.
            # addRow("", w) creates NO label widget at all, so "no widget" has
            # to count as an empty label column – otherwise the row below is
            # padded to a full control height for nothing.
            has_text = lbl is not None and (not isinstance(lbl, QLabel)
                                            or bool(lbl.text()))
            if lbl is not None and has_text and lbl.sizeHint().height() <= row_h:
                lbl.setFixedHeight(row_h)      # never stretched by the layout
            # A checkbox or slider is only ~20px tall by default – a wide but
            # very flat target.  A self-captioned checkbox used to be left at
            # that height to keep lists of them compact, which was the wrong
            # trade: six such rows 8px apart mean a vertical overshoot lands
            # on the NEIGHBOURING setting and silently changes it.  Missing is
            # recoverable, hitting the wrong one is not, so every checkbox now
            # gets a full control row (see tests/test_click_targets.py).
            fitem = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            field = fitem.widget() if fitem is not None else None
            if isinstance(field, QSlider):
                field.setMinimumHeight(row_h)

        # A run of self-captioned checkboxes reads as ONE list.  Giving each a
        # full control row (for the click target) and then adding the form's
        # spacing on top drifted them so far apart that the block took half a
        # screen and the explanation below no longer looked attached to the
        # switch above it.  Rows that touch still keep a 44px+ centre-to-centre
        # pitch, so the target is unchanged – only the air between is gone.
        boxes = 0
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if item is not None and isinstance(item.widget(), QCheckBox):
                boxes += 1
        if boxes >= 3:
            form.setVerticalSpacing(0)

        widest = 0
        rows: list[tuple[QWidget, int]] = []
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if item is None:
                continue
            w = item.widget()
            if isinstance(w, (QComboBox, QAbstractSpinBox)):
                # A combo's own hint under-reports (see combo_needed_width);
                # use the corrected value so nothing is clipped.
                need = (combo_needed_width(w) if isinstance(w, QComboBox)
                        else w.sizeHint().width())
                widest = max(widest, need)
                rows.append((w, need))
        # Cap it: one unusually long entry (e.g. "Cloud-Dienst (OpenRouter,
        # OpenAI, Groq …)") would otherwise raise the MINIMUM width of every
        # field in the form, which in turn forces a minimum width on the whole
        # page – the cards then no longer fit beside the sidebar on a smaller
        # window and the page starts scrolling sideways.  Beyond the cap the
        # long field simply keeps its own natural width.
        widest = min(widest, em(16))
        for w, need in rows:
            # Never below what the widget actually needs – the shared width is
            # about looking consistent, not about clipping a long entry.
            w.setMinimumWidth(max(min(widest, need), need))

    # Final pass, deferred until the layout has actually run: match every
    # single-line row's label height to its field's REAL height.  A control's
    # exact height is a stylesheet detail (a spin box ends up a few px taller
    # than a combo box because of its stepper buttons), so guessing it up
    # front leaves small offsets that are very visible down a column of rows.
    # Tall fields keep the one-row label set above – that is what anchors
    # their caption to the FIRST row and lets the field grow downwards.
    def _match_label_heights() -> None:
        import shiboken6
        if not shiboken6.isValid(root):
            return
        fix_combo_widths(root)
        for form in root.findChildren(QFormLayout):
            for i in range(form.rowCount()):
                li = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                fi = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                lbl = li.widget() if li is not None else None
                field = fi.widget() if fi is not None else None
                if lbl is None or field is None:
                    continue
                if isinstance(lbl, QLabel) and not lbl.text():
                    continue
                fh = field.height()
                if 0 < fh <= em(4):            # a single-line control
                    lbl.setFixedHeight(fh)

    QTimer.singleShot(0, _match_label_heights)

    # Controls sitting side by side in one row (a dropdown and its refresh
    # button, a spin box and its copy button …) are centred on each other.
    # Without this they are top-aligned within the row and end up a few px
    # apart, which breaks the clean baseline down a column of settings.
    # Decided by widget TYPE, not by measured height, so it also works for
    # pages that have not been laid out yet.
    _INLINE = (QComboBox, QAbstractSpinBox, QPushButton, QCheckBox)
    for hbox in root.findChildren(QHBoxLayout):
        items = [hbox.itemAt(k) for k in range(hbox.count())]
        widgets = [it.widget() for it in items if it.widget() is not None]
        if len(widgets) < 2 or not all(isinstance(w, _INLINE) for w in widgets):
            continue
        for it in items:
            if it.widget() is not None:
                it.setAlignment(Qt.AlignmentFlag.AlignVCenter)
