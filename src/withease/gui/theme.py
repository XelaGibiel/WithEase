"""Theme handling – light / dark / system, contrast level and font size.

This module is the single source of truth for every colour that is not taken
straight from the Qt palette.  Pages are rebuilt after a theme change, so the
style helpers below are evaluated at build time and always match the active
scheme – hint texts, warnings and selection colours stay readable in light
AND dark mode (WCAG-oriented contrast, see the accessibility notes per value).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollArea,
)


class _WheelGuard(QObject):
    """Stops the mouse wheel from silently changing a dropdown / spin box that
    the user is only scrolling *past*.

    A combo box changes its value on a wheel tick even when it merely sits under
    the cursor, so scrolling a settings page flips a setting by accident.  A
    dropdown must be changed *deliberately* – by opening it and picking – so its
    wheel tick is always swallowed (and handed to the enclosing scroll area, so
    the page scrolls instead).  When the popup list is open the wheel targets
    that list, not the combo, so scrolling the open list still works.  A spin
    box is guarded the same way while it is not focused; once clicked into it,
    the wheel may adjust it."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            block = (isinstance(obj, QComboBox)
                     or (isinstance(obj, QAbstractSpinBox)
                         and not obj.hasFocus()))
            if block:
                p = obj.parentWidget()
                while p is not None and not isinstance(p, QScrollArea):
                    p = p.parentWidget()
                if p is not None:
                    QApplication.sendEvent(p.viewport(), event)
                return True      # never let the control consume it
        return False


class _ToolTipKeeper(QObject):
    """Keeps a tool-tip open for as long as the pointer stays on the element.

    Qt hides a tip on its own timer (about 10 s plus a little per character).
    The explanations in this app are whole sentences – often two, comparing
    two options – and reading them is exactly the point, so a tip vanishing
    mid-sentence forces the user to move the mouse away and back to start over.
    For people who find precise pointing hard, that is a real cost.

    Re-showing the tip with an explicit hold time and the widget's own rect
    hands the decision back to the pointer: Qt keeps the tip while the cursor
    is inside that rect and hides it the moment it leaves.  The hold is long
    rather than infinite so a tip can never be stranded on screen.
    """

    HOLD_MS = 10 * 60 * 1000        # effectively "until you move away"

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.ToolTip:
            return False
        from PySide6.QtWidgets import QAbstractItemView, QToolTip, QWidget
        if not isinstance(obj, QWidget):
            return False

        # An item view keeps its tips on the ITEMS, not on the viewport, so
        # look the hovered entry up itself – this is what carries the
        # per-entry explanations in the dropdowns (ui_utils.set_option_hint).
        view = obj.parentWidget() if obj.parentWidget() else None
        if isinstance(view, QAbstractItemView) and obj is view.viewport():
            index = view.indexAt(event.pos())
            if not index.isValid():
                return False
            text = index.data(Qt.ItemDataRole.ToolTipRole)
            if not text:
                return False
            QToolTip.showText(event.globalPos(), str(text), obj,
                              view.visualRect(index), self.HOLD_MS)
            return True

        text = obj.toolTip()
        if not text:
            # Let Qt walk up to a parent that has one, or let a widget that
            # builds its tip on the fly handle the event itself.
            return False
        QToolTip.showText(event.globalPos(), text, obj, obj.rect(),
                          self.HOLD_MS)
        return True


_SCHEMES = {
    "system": Qt.ColorScheme.Unknown,   # follow the OS setting
    "light": Qt.ColorScheme.Light,
    "dark": Qt.ColorScheme.Dark,
}

# Current resolved state (set by apply_theme)
_state: dict = {
    "dark": True,
    "contrast": "normal",
    "font_pt": 0,
}
_default_font_pt: int | None = None


# ---------------------------------------------------------------------------
# Colour accessors – always call these at widget BUILD time (pages are
# rebuilt on theme change, so the values are per-theme correct).
# ---------------------------------------------------------------------------

def is_dark() -> bool:
    return bool(_state["dark"])


def high_contrast() -> bool:
    return _state["contrast"] == "high"


def accent() -> str:
    """Accent colour: orange on dark backgrounds, dark blue on light ones."""
    if high_contrast():
        return "#FFD75E" if is_dark() else "#00329B"
    return "#E65100" if is_dark() else "#1B4F9C"


def accent_fg() -> str:
    """Readable text colour on top of an accent() fill (e.g. a checked toggle
    or the active nav pill).  White on the blue/orange accents; black on the
    yellow high-contrast accent."""
    if high_contrast():
        return "#000000" if is_dark() else "#FFFFFF"
    return "#FFFFFF"


def hint_color() -> str:
    """Secondary text.  Contrast ≥ 4.5:1 against the window background."""
    if high_contrast():
        return "#FFFFFF" if is_dark() else "#000000"
    return "#A9B2C3" if is_dark() else "#44505C"


def warn_color() -> str:
    """Warning/error text, readable on the respective background."""
    if high_contrast():
        return "#FF6B6B" if is_dark() else "#B00000"
    return "#FFA24D" if is_dark() else "#A63A00"


def ok_color() -> str:
    """Positive/active state (e.g. running profile).  ≥ 4.5:1 contrast."""
    if high_contrast():
        return "#00FF66" if is_dark() else "#006400"
    return "#4CAF50" if is_dark() else "#1B7A2E"


def danger_color() -> str:
    """Stopped/paused state (e.g. emergency stop).  ≥ 4.5:1 contrast."""
    if high_contrast():
        return "#FF6B6B" if is_dark() else "#B00000"
    return "#EF5350" if is_dark() else "#C62828"


def action_color() -> str:
    """Neutral "do something" blue for small action icons (refresh/apply).

    Deliberately NOT accent(): the accent is orange in the dark theme, which
    on a small refresh glyph read as a warning.  A blue reads as "update /
    fetch again" in both schemes.  ≥ 4.5:1 contrast on the card surface."""
    if high_contrast():
        return "#7FD4FF" if is_dark() else "#00329B"
    return "#4FA8E8" if is_dark() else "#1565C0"


def target_px() -> int:
    """Height of one control row / minimum click-target edge, in px.

    44px is the WCAG 2.5.5 (AAA) target size and the floor.  Above it the
    value GROWS with the font-size setting: someone who picks a larger font
    is telling us they need bigger things, and it would be odd to enlarge
    the text but keep the buttons small.  The factor is tuned so the floor
    holds up to ~11pt and every larger step widens the targets."""
    return max(44, round(_line_px() * 2.8))


def refresh_icon(color: str, size: int = 64) -> QIcon:
    """A slim circular-arrow "refresh" icon drawn in `color`.

    Drawn here rather than tinting QStyle's SP_BrowserReload: that stock icon
    is a heavy, filled two-tone glyph that looked bold and clunky next to the
    thin text around it.  A stroked arc gives us the line weight, and it stays
    crisp at any size because it is rendered at the requested size."""
    from math import cos, radians, sin

    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QPainter, QPen, QPixmap, QPolygonF

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    col = QColor(color)
    stroke = size * 0.085              # thin – the whole point of this icon
    margin = size * 0.22
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

    pen = QPen(col)
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Leave a gap at the lower right for the arrow head to sit in.
    start_deg, span_deg = 40.0, 265.0
    p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    # Arrow head at the arc's end, pointing along the (anticlockwise) tangent.
    end_deg = start_deg + span_deg
    cx, cy = rect.center().x(), rect.center().y()
    r = rect.width() / 2.0
    a = radians(end_deg)
    tipbase = QPointF(cx + r * cos(a), cy - r * sin(a))
    dx, dy = -sin(a), -cos(a)          # unit tangent, anticlockwise
    h, w = size * 0.22, size * 0.15
    perp = QPointF(-dy, dx)
    head = QPolygonF([
        QPointF(tipbase.x() + dx * h, tipbase.y() + dy * h),
        QPointF(tipbase.x() + perp.x() * w, tipbase.y() + perp.y() * w),
        QPointF(tipbase.x() - perp.x() * w, tipbase.y() - perp.y() * w),
    ])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col)
    p.drawPolygon(head)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Surface colours – the calm, card-based desktop look.  One place, three
# schemes.  High contrast keeps pure black/white with strong borders; light and
# dark use soft, cool tones.  All pairs are WCAG-oriented (text ≥ 4.5:1 on its
# surface, borders/focus clearly visible).
# ---------------------------------------------------------------------------

def _surfaces() -> dict:
    if high_contrast():
        if is_dark():
            return {"win": "#000000", "side": "#000000", "card": "#000000",
                    "border": "#FFFFFF", "control": "#000000",
                    "navbg": "#FFD75E", "navfg": "#000000",
                    "hover": "#1A1A1A", "listalt": "#1E1E1E", "text": "#FFFFFF"}
        return {"win": "#FFFFFF", "side": "#FFFFFF", "card": "#FFFFFF",
                "border": "#000000", "control": "#FFFFFF",
                "navbg": "#00329B", "navfg": "#FFFFFF",
                "hover": "#E8E8E8", "listalt": "#DDDDDD", "text": "#000000"}
    if is_dark():
        return {"win": "#171B22", "side": "#1E232C", "card": "#252B36",
                "border": "#39414F", "control": "#2B313D",
                "navbg": "#2C3646", "navfg": "#8FB6F2",
                "hover": "#2A303C", "listalt": "#2E3644", "text": "#E6EAF2"}
    return {"win": "#EEF2F7", "side": "#FFFFFF", "card": "#FFFFFF",
            "border": "#D3DCE8", "control": "#FFFFFF",
            "navbg": "#E7F0FB", "navfg": "#1B4F9C",
            "hover": "#F2F6FC", "listalt": "#EAEFF6", "text": "#1B2430"}


def window_bg() -> str:
    return _surfaces()["win"]


def card_bg() -> str:
    return _surfaces()["card"]


def border_color() -> str:
    return _surfaces()["border"]


def _line_px() -> int:
    """Current font line height in pixels – used to size controls so they grow
    with the font-size setting (WCAG target size)."""
    from PySide6.QtGui import QFontMetrics
    app = QApplication.instance()
    return QFontMetrics(app.font()).height() if app else 16


def _chevron_path(direction: str) -> str:
    """A crisp chevron PNG in the current text colour, cached per colour, for
    the combobox / spinbox arrows (so they are always clearly visible, unlike
    the low-contrast native arrow).  Returns a forward-slashed path for QSS."""
    import os
    import tempfile
    color = _surfaces()["text"]
    icon_dir = os.path.join(tempfile.gettempdir(), "withease_icons")
    try:
        os.makedirs(icon_dir, exist_ok=True)
    except OSError:
        return ""
    path = os.path.join(icon_dir, f"chevron_{direction}_{color.lstrip('#')}.png")
    if not os.path.exists(path):
        from PySide6.QtCore import QPointF, Qt as _Qt
        from PySide6.QtGui import (QColor, QPainter, QPen, QPixmap, QPolygonF)
        pm = QPixmap(32, 32)
        pm.fill(_Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidth(3)
        pen.setCapStyle(_Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(_Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if direction == "up":
            pts = [QPointF(9, 20), QPointF(16, 12), QPointF(23, 20)]
        else:
            pts = [QPointF(9, 13), QPointF(16, 21), QPointF(23, 13)]
        p.drawPolyline(QPolygonF(pts))
        p.end()
        pm.save(path)
    return path.replace("\\", "/")


def _checkbox_icon(checked: bool) -> str:
    """A crisp checkbox indicator PNG for the current theme, cached.

    Unchecked = an empty rounded box with a clearly-visible neutral border;
    checked = an accent-filled rounded box with a contrasting check mark.  The
    native Fusion indicator was too subtle in dark mode (an empty dark box on a
    dark card was hard to tell from a filled one), so we draw it ourselves."""
    import os
    import tempfile
    s = _surfaces()
    acc, afg, brd, ctl = accent(), accent_fg(), hint_color(), s["control"]
    icon_dir = os.path.join(tempfile.gettempdir(), "withease_icons")
    try:
        os.makedirs(icon_dir, exist_ok=True)
    except OSError:
        return ""
    key = f"{'on' if checked else 'off'}_{acc}_{afg}_{brd}_{ctl}".replace("#", "")
    path = os.path.join(icon_dir, f"cb_{key}.png")
    if not os.path.exists(path):
        from PySide6.QtCore import QPointF, QRectF, Qt as _Qt
        from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
        pm = QPixmap(40, 40)
        pm.fill(_Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(4, 4, 32, 32)
        if checked:
            p.setPen(QPen(QColor(acc), 2))
            p.setBrush(QColor(acc))
            p.drawRoundedRect(box, 8, 8)
            pen = QPen(QColor(afg))
            pen.setWidth(5)
            pen.setCapStyle(_Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(_Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QPolygonF(
                [QPointF(12, 21), QPointF(18, 27), QPointF(29, 13)]))
        else:
            p.setPen(QPen(QColor(brd), 3))
            p.setBrush(QColor(ctl))
            p.drawRoundedRect(box, 8, 8)
        p.end()
        pm.save(path)
    return path.replace("\\", "/")


def app_stylesheet() -> str:
    """The one central stylesheet for the calm, card-based desktop look.

    Derives every colour from the active scheme (light / dark / high contrast),
    so there is a single styling system.  Controls share one visual language
    (radius, border, hover / pressed / focus, disabled) and the keyboard focus
    ring is strong and visible in every scheme (WCAG 2.4.7 / 2.4.11)."""
    s = _surfaces()
    acc = accent()
    dgr = danger_color()
    r = 8
    # Never below the accessible target edge: at the smaller font sizes
    # 1.55 line-heights came out at ~36px, i.e. an under-sized target for
    # every dropdown and text field in the app.
    row_h = target_px()          # the total height every control should have
    # Qt's box model puts padding + border OUTSIDE min-height, so subtract
    # each rule's own padding to land on the same total for all of them.
    ctl_h = row_h - 10           # inputs:  padding 4px  + 1px border
    btn_h = row_h - 14           # buttons: padding 6px  + 1px border
    icn_h = row_h - 6            # icon btn: padding 2px + 1px border
    # Tool-tip padding grows with the font, like every other
    # spacing in this stylesheet.
    tip_pad_v = max(6, round(_line_px() * 0.42))
    tip_pad_h = max(8, round(_line_px() * 0.55))
    emg_h = round(_line_px() * 2.1)
    gb_pad = round(_line_px() * 2.5)      # room for the inside group-box title
    gb_top = round(_line_px() * 0.7)
    chevron_down = _chevron_path("down")
    chevron_up = _chevron_path("up")
    ind = max(16, round(_line_px() * 0.95))     # checkbox size, scales with font
    cb_off = _checkbox_icon(False)
    cb_on = _checkbox_icon(True)
    return f"""
        QMainWindow, QDialog {{ background: {s['win']}; }}

        QWidget#sidebar {{ background: {s['side']};
            border-right: 1px solid {s['border']}; }}
        QLabel#logo {{ font-size: {_font_px(4)}pt; font-weight: bold;
            color: {acc}; padding: 2px 4px; }}

        /* Sidebar navigation: rounded active pill with a left accent bar –
           colour is NOT the only cue (bold text + the bar + the tint). */
        QListWidget#nav {{ background: {s['side']}; border: none; outline: 0;
            font-size: {_font_px()}pt; }}
        QListWidget#nav::item {{
            color: {s['text']};
            border: none; border-left: 4px solid transparent;
            border-radius: 8px; padding: 8px 10px; margin: 2px 0;
        }}
        QListWidget#nav::item:hover {{ background: {s['hover']}; }}
        QListWidget#nav::item:selected {{
            background: {s['navbg']}; color: {s['navfg']};
            border-left: 4px solid {acc}; font-weight: bold;
        }}

        /* Cards – anything marked with objectName "card". */
        QFrame#card, QWidget#card {{
            background: {s['card']};
            border: 1px solid {s['border']};
            border-radius: 12px;
            padding: 16px 18px 14px 18px;
        }}
        /* QGroupBox is a card too – title sits INSIDE at the top-left as a
           header (like the General page cards), not on the border notch. */
        QGroupBox {{
            background: {s['card']};
            border: 1px solid {s['border']};
            border-radius: 12px;
            margin-top: 0px;
            padding: {gb_pad}px 18px 14px 18px;
        }}
        QGroupBox::title {{
            subcontrol-origin: padding; subcontrol-position: top left;
            top: {gb_top}px; left: 18px; padding: 0;
            color: {s['text']}; font-weight: bold; font-size: {_font_px(1)}pt;
        }}
        QGroupBox::indicator {{ width: 16px; height: 16px; }}
        QLabel#cardTitle {{ font-weight: bold; font-size: {_font_px(1)}pt;
            color: {s['text']}; }}
        QLabel#cardTitleDanger {{ font-weight: bold; font-size: {_font_px(1)}pt;
            color: {dgr}; }}
        /* Card/section icons: a fixed size on purpose – unlike the title text
           they must NOT grow with the font-size setting (an oversized emoji
           next to normal-sized text looked unbalanced), while still scaling
           with the OS display scale like everything else (pt, not px). */
        QLabel#cardIcon {{ font-size: 15pt; color: {s['text']}; }}
        QLabel#cardIconDanger {{ font-size: 15pt; color: {dgr}; }}
        /* Hint ("ⓘ") icons – same fixed-size convention as cardIcon, but in
           the secondary hint colour so they read as auxiliary, not primary.
           A transparent border reserves the space so focus doesn't shift the
           layout by 1px (same trick as QCheckBox above) – hint icons are
           keyboard-focusable (see widgets/hint_icon.py) so this ring must be
           visible, not just the mouse-hover tooltip. */
        QLabel#hintIcon {{
            font-size: 15pt; color: {hint_color()};
            border: 1px solid transparent; border-radius: 4px; padding: 0px 2px;
        }}
        QLabel#hintIcon:focus {{ border-color: {acc}; color: {acc}; }}

        /* Tool-tips are where every explanation in this app ends up, so they
           are a reading surface, not a one-word label: real padding, the card
           background and a border so the text does not float on the desktop.
           Width, line spacing and paragraph breaks come from the rich-text
           wrapper (ui_utils.wrap_tooltip).

           The border is the ACCENT colour: a tip always appears on top of the
           cards it explains, and a neutral border is the same colour as those
           cards' own edges, so it melted into the page.  Accent also ties the
           tip to the ⓘ that opened it (which turns accent on focus).  Kept at
           1px on purpose – 2px accent is the search-hit outline, and one
           signal should not be able to look like another. */
        QToolTip {{
            background: {s['card']}; color: {s['text']};
            border: 1px solid {acc}; border-radius: {r}px;
            padding: {tip_pad_v}px {tip_pad_h}px;
            /* Pin the size in the stylesheet itself.  A QSS rule that matches
               a widget makes Qt re-polish its font, and a rule WITHOUT a
               font-size resets it to the style default – which silently undoes
               the QToolTip.setFont() below and leaves tips at the system size
               whatever the user picked.  (Same trap as the item views further
               down, see selection_qss.) */
            font-size: {_font_px()}pt;
        }}

        /* Unified controls. */
        QPushButton {{
            background: {s['control']}; color: {s['text']};
            border: 1px solid {s['border']}; border-radius: {r}px;
            padding: 6px 14px; min-height: {btn_h}px;
        }}
        QPushButton:hover {{ background: {s['hover']}; border-color: {acc}; }}
        QPushButton:pressed {{ background: {s['navbg']}; }}
        QPushButton:disabled {{ color: {hint_color()}; border-color: {s['border']}; }}
        /* Checkable toggle buttons (e.g. keyboard exception keys): the active
           state is the accent with readable text – blue in light, not orange. */
        QPushButton:checked {{
            background: {acc}; color: {accent_fg()}; border-color: {acc};
        }}
        /* NOTE: deliberately no font-weight change on :checked.  Qt sizes the
           button from its NORMAL-weight text, so switching to bold when it is
           selected made the label wider than the button and clipped the last
           letter ("Dunkel" → "Dunke").  The accent fill already marks the
           selection unmistakably. */
        /* Small icon-only buttons (✕, ▲▼): no wide padding so the glyph fits,
           but a full accessible click target (see `tgt`).  This used to say
           min-width:0, which silently overrode the widgets' own
           setFixedWidth() and left ~20px-wide buttons – far below even the
           WCAG AA minimum, on the very pages these users need most. */
        QPushButton[iconBtn="true"] {{
            padding: 2px; font-weight: bold;
            min-width: {icn_h}px; min-height: {icn_h}px;
        }}
        /* A setting jumped to from the search is outlined for a moment so the
           eye can find it on a page full of similar-looking rows. */
        *[searchHit="true"] {{
            border: 2px solid {acc}; border-radius: {r}px;
        }}

        /* Remove/clear buttons carry their meaning in colour, not just in the
           glyph – a plain grey ✕ read as decoration.  Disabled falls back to
           the muted hint colour so it still looks inactive. */
        QPushButton[dangerIcon="true"] {{ color: {dgr}; }}
        QPushButton[dangerIcon="true"]:hover {{
            color: {dgr}; border-color: {dgr};
        }}
        QPushButton[dangerIcon="true"]:disabled {{ color: {hint_color()}; }}

        /* Text buttons that DELETE something.  Tinted, not filled: the point is
           "stop and read this", not "alarm".  A filled red button next to the
           harmless ones would pull the eye to the most destructive action on
           the page, which is the opposite of what is wanted. */
        QPushButton[danger="true"] {{
            color: {dgr}; border-color: {dgr};
        }}
        QPushButton[danger="true"]:hover {{
            background: {dgr}; color: {accent_fg()}; border-color: {dgr};
        }}
        QPushButton[danger="true"]:disabled {{
            color: {hint_color()}; border-color: {s['border']};
        }}

        QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox,
        QPlainTextEdit, QTextEdit {{
            background: {s['control']}; color: {s['text']};
            border: 1px solid {s['border']}; border-radius: {r}px;
            padding: 4px 8px; min-height: {ctl_h}px;
            /* Selected/marked text uses the accent (blue in light, orange in
               dark) instead of the native highlight – one accent per theme. */
            selection-background-color: {acc};
            selection-color: {accent_fg()};
        }}
        /* Reserve room on the right for the sub-controls drawn INSIDE the
           field (the 24px drop-down arrow, the 22px spin buttons).  Qt does
           not add their width to sizeHint(), so without this the last
           characters of the longest entry disappeared under the arrow –
           "Deutsch" rendered as "Deutsc". */
        QComboBox {{ padding-right: 30px; }}
        QSpinBox, QDoubleSpinBox {{ padding-right: 28px; }}
        QComboBox:hover, QLineEdit:hover, QSpinBox:hover,
        QDoubleSpinBox:hover {{ border-color: {acc}; }}
        QComboBox QAbstractItemView {{
            background: {s['card']}; color: {s['text']};
            border: 1px solid {s['border']}; border-radius: 6px;
            selection-background-color: {s['navbg']};
            selection-color: {s['navfg']};
            outline: 0;
        }}
        /* Dropdown / spin arrows: borderless (no square button on the rounded
           field), a clear chevron drawn in the text colour. */
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: center right;
            width: 24px; border: none; background: transparent;
        }}
        QComboBox::down-arrow {{
            image: url({chevron_down}); width: 12px; height: 12px;
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border; subcontrol-position: top right;
            width: 22px; border: none; border-left: 1px solid {s['border']};
            background: transparent;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border; subcontrol-position: bottom right;
            width: 22px; border: none; border-left: 1px solid {s['border']};
            background: transparent;
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            image: url({chevron_up}); width: 11px; height: 11px;
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            image: url({chevron_down}); width: 11px; height: 11px;
        }}

        /* Sliders: the filled part + handle use the accent (blue in light). */
        QSlider::groove:horizontal {{
            height: 6px; border-radius: 3px; background: {s['border']};
        }}
        QSlider::sub-page:horizontal {{
            height: 6px; border-radius: 3px; background: {acc};
        }}
        QSlider::handle:horizontal {{
            width: 16px; margin: -6px 0; border-radius: 8px;
            background: {acc}; border: 2px solid {s['card']};
        }}

        /* Strong, always-visible keyboard focus (WCAG 2.4.7 / 2.4.11). */
        QPushButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus,
        QDoubleSpinBox:focus,
        QListWidget:focus, QTableWidget:focus, QSlider:focus {{
            border: 2px solid {acc};
        }}
        /* Check/radio focus: a soft rounded pill (like the nav) with a thin
           accent outline – clearly visible but not the hard rectangle it was.
           The always-present transparent border keeps the layout from jumping
           by 1px when focus arrives. */
        QCheckBox, QRadioButton {{
            border: 1px solid transparent; border-radius: 6px; padding: 1px 3px;
        }}
        QCheckBox:focus, QRadioButton:focus {{
            background: {s['navbg']}; border-color: {acc};
        }}
        /* Clear checked/unchecked distinction (native was too subtle in dark):
           empty box with a visible border vs. accent box + check mark. */
        QCheckBox::indicator {{ width: {ind}px; height: {ind}px; }}
        QCheckBox::indicator:unchecked {{ image: url({cb_off}); }}
        QCheckBox::indicator:checked {{ image: url({cb_on}); }}

        /* Emergency stop – bordered danger button, not a solid alarm block. */
        QPushButton#emergencyButton {{
            background: {s['card']}; color: {dgr};
            border: 2px solid {dgr}; border-radius: {r}px;
            padding: 8px 12px; font-weight: bold; min-height: {emg_h}px;
        }}
        QPushButton#emergencyButton:hover {{ background: {s['hover']}; }}

        /* Menus (e.g. the tray popup): card surface, accent-tinted selection –
           blue in light, not the native/palette highlight. */
        QMenu {{
            background: {s['card']}; color: {s['text']};
            border: 1px solid {s['border']}; border-radius: 8px; padding: 4px;
            /* Pin the size here, for the same reason as QToolTip above: a QSS
               rule that matches a widget makes Qt re-polish its font, and a
               rule WITHOUT a font-size resets it to the style default.  That
               is why the tray menu ignored the font-size setting until it was
               changed once at runtime (which rebuilt the menu after the
               stylesheet was already in place). */
            font-size: {_font_px()}pt;
        }}
        QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 6px; }}
        QMenu::item:selected {{
            background: {s['navbg']}; color: {s['navfg']};
        }}
        QMenu::separator {{ height: 1px; background: {s['border']};
            margin: 4px 8px; }}

        QWidget#footer {{ background: transparent;
            border-top: 1px solid {s['border']}; }}

        /* Dictation history: a rounded, bordered card-style list (matching the
           rest of the app) with alternating row tint + a divider between
           entries, so it is easy to tell where one dictation ends. */
        QLabel#dictHistoryHeader {{
            font-weight: bold; color: {s['text']}; padding: 2px 2px 4px 2px;
        }}
        QListWidget#dictHistory {{
            background: {s['control']};
            alternate-background-color: {s['listalt']};
            border: 1px solid {s['border']}; border-radius: 8px;
            padding: 2px; outline: 0;
        }}
        /* Generous left/right inset: the entry text used to start flush
           against the list's left edge, which read as cramped next to the
           roomier padding everywhere else. */
        QListWidget#dictHistory::item {{
            border-bottom: 1px solid {s['border']};
            border-radius: 4px; padding: 8px 12px;
        }}
        QListWidget#dictHistory::item:selected {{
            background: {s['navbg']}; color: {s['navfg']};
        }}

        /* Slim, calm scrollbars matching the app – used by the sidebar nav
           and every scroll area (content pages, dictation history …). */
        QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
        QScrollBar::handle:vertical {{
            background: {s['border']}; border-radius: 5px;
            min-height: 28px; margin: 2px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {acc}; }}
        QScrollBar:horizontal {{ background: transparent; height: 12px;
            margin: 0; }}
        QScrollBar::handle:horizontal {{
            background: {s['border']}; border-radius: 5px;
            min-width: 28px; margin: 2px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {acc}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

        QScrollArea {{ border: none; background: transparent; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
    """


def _font_px(delta: int = 0) -> int:
    app = QApplication.instance()
    base = app.font().pointSize() if app else 9
    return max(8, base + delta)


def hint_style(extra: str = "") -> str:
    """Stylesheet for secondary/description labels."""
    return (f"color: {hint_color()}; font-size: {_font_px(-1)}pt; {extra}")


def warn_style() -> str:
    """Stylesheet for warning labels (hotkey conflicts etc.)."""
    return f"color: {warn_color()}; font-size: {_font_px(-1)}pt;"


def title_style() -> str:
    """Page/section titles – same as the module enable-checkboxes."""
    return f"font-weight: bold; font-size: {_font_px(2)}pt;"


def selection_qss(cls: str) -> str:
    """Item-view selection: readable in every theme (dark text on a light
    tint / theme-matching accent), no stray native accent bars."""
    if high_contrast():
        bg, fg = (accent(), "#000000") if is_dark() else (accent(), "#FFFFFF")
    elif is_dark():
        bg, fg = "#F2B27C", "#000000"   # pale orange, black text
    else:
        bg, fg = "#BDD3EF", "#000000"   # pale blue, black text
    # Pin the row backgrounds to our own theme surfaces.  Otherwise a table with
    # alternating rows inherits the palette's AlternateBase, which on Windows can
    # be tinted by the system accent – that is what painted every *odd* row a
    # stray red on the dark theme.
    s = _surfaces()
    # Pin the font size in the stylesheet itself.  A stylesheet value survives
    # the widget re-polish that happens when a list/table is reparented into
    # the window, whereas a plain setFont() on a stylesheet-styled item view
    # gets reset back to the system size (this is why the sidebar text only
    # followed the font setting after the user changed it a second time).
    return f"""
        {cls} {{ outline: 0; font-size: {_font_px()}pt;
            background: {s['control']};
            alternate-background-color: {s['listalt']}; }}
        {cls}::item {{
            border: none;
            padding: 2px 4px;
        }}
        {cls}::item:selected, {cls}::item:selected:active,
        {cls}::item:selected:!active, {cls}::item:focus {{
            background-color: {bg};
            color: {fg};
            border: none;
        }}
    """


def style_item_view(view, cls: str) -> None:
    """Apply the selection stylesheet to a list/table AND keep it on the
    application font.

    On the Windows style, calling ``setStyleSheet`` on an item view detaches it
    from the inherited application font, so it keeps the system font size until
    it is re-polished.  That is why the sidebar text used to stay at the default
    size after start-up and only resized once the user changed the font again
    (which re-polished the existing widget).  Re-asserting the app font right
    after the stylesheet makes every list/table follow the font-size setting
    from the first build.
    """
    view.setStyleSheet(selection_qss(cls))
    app = QApplication.instance()
    if app is not None:
        view.setFont(app.font())


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def _resolve_dark(qt_app: QApplication, name: str) -> bool:
    if name == "dark":
        return True
    if name == "light":
        return False
    # system: read the effective palette after the scheme was applied
    return qt_app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _high_contrast_palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        bg, fg, base, acc = "#000000", "#FFFFFF", "#000000", "#FFD75E"
        acc_fg = "#000000"
    else:
        bg, fg, base, acc = "#FFFFFF", "#000000", "#FFFFFF", "#00329B"
        acc_fg = "#FFFFFF"
    for role, color in (
        (QPalette.ColorRole.Window, bg),
        (QPalette.ColorRole.WindowText, fg),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.AlternateBase, bg),
        (QPalette.ColorRole.Text, fg),
        (QPalette.ColorRole.Button, bg),
        (QPalette.ColorRole.ButtonText, fg),
        (QPalette.ColorRole.ToolTipBase, bg),
        (QPalette.ColorRole.ToolTipText, fg),
        (QPalette.ColorRole.Highlight, acc),
        (QPalette.ColorRole.HighlightedText, acc_fg),
        (QPalette.ColorRole.PlaceholderText, fg),
        (QPalette.ColorRole.Mid, fg),
    ):
        p.setColor(role, QColor(color))
    return p


def apply_theme(qt_app: QApplication, name: str,
                contrast: str = "normal", font_pt: int = 0) -> None:
    """Apply colour scheme + contrast level + global font size."""
    global _default_font_pt
    if _default_font_pt is None:
        _default_font_pt = qt_app.font().pointSize()

    # The Fusion style honours our stylesheet consistently across light / dark /
    # high contrast (native Windows style drops combobox arrows and ignores many
    # QSS rules).  Set once; it is the base the app_stylesheet() builds on.
    if not _state.get("_style_set"):
        try:
            from PySide6.QtWidgets import QStyleFactory
            qt_app.setStyle(QStyleFactory.create("Fusion"))
        except Exception:
            pass
        _state["_style_set"] = True

    # Guard every dropdown / spin box against accidental wheel changes – one
    # app-wide filter covers the core UI and every module's controls.
    if not _state.get("_wheel_guard"):
        guard = _WheelGuard(qt_app)
        qt_app.installEventFilter(guard)
        _state["_wheel_guard"] = guard

    # Tool-tips stay up while the pointer rests on the element they belong to.
    if not _state.get("_tip_keeper"):
        keeper = _ToolTipKeeper(qt_app)
        qt_app.installEventFilter(keeper)
        _state["_tip_keeper"] = keeper

    # Font size first (styles below derive sizes from it).  0 = system size.
    font = qt_app.font()
    font.setPointSize(font_pt if font_pt and font_pt > 0 else _default_font_pt)
    qt_app.setFont(font)
    # Tool-tips keep their own font registration in Qt and do not always follow
    # a later setFont(), so pin them to the chosen size explicitly – otherwise a
    # tip popping up over any window ignores the configured font size.
    try:
        from PySide6.QtWidgets import QToolTip
        QToolTip.setFont(font)
    except Exception:
        pass

    scheme = _SCHEMES.get(name, Qt.ColorScheme.Unknown)
    try:
        qt_app.styleHints().setColorScheme(scheme)
    except Exception:
        pass
    # Only "system" has to pump the event loop here – _resolve_dark then reads
    # the palette Qt just recomputed.  For explicit light/dark the answer is
    # already known, and pumping events mid-switch repainted every open window
    # with the NEW palette but the OLD stylesheet, i.e. a visible flash.
    if name not in ("light", "dark"):
        qt_app.processEvents()

    dark = _resolve_dark(qt_app, name)
    _state.update({"dark": dark, "contrast": contrast, "font_pt": font_pt})

    # Check/radio indicators do not follow the font in Qt – scale them via
    # stylesheet so they grow with a custom font size (WCAG target size).
    # At system size no rule is set, keeping the native look untouched.
    base_qss = ""
    if font_pt and font_pt >= 8:
        from PySide6.QtGui import QFontMetrics
        px = max(16, round(QFontMetrics(qt_app.font()).height() * 0.85))
        base_qss = (
            "QCheckBox::indicator, QRadioButton::indicator,"
            " QGroupBox::indicator, QListWidget::indicator,"
            " QTableWidget::indicator"
            f" {{ width: {px}px; height: {px}px; }}")

    if contrast == "high":
        qt_app.setPalette(_high_contrast_palette(dark))
        qt_app.setStyleSheet(base_qss + app_stylesheet())
    else:
        qt_app.setPalette(QPalette())   # back to the scheme's own palette
        try:
            qt_app.styleHints().setColorScheme(scheme)
        except Exception:
            pass
        qt_app.processEvents()          # let the scheme's palette settle
        # Pin the selection (Highlight) colour to the app's own theme colour so
        # it never inherits the Windows *system accent*.  Otherwise a selected
        # item-view row that isn't the focused widget is painted by the native
        # style with the user's Windows accent (e.g. dark red) instead of our
        # pale tint – set it for Active/Inactive/Disabled so it stays constant.
        pal = qt_app.palette()
        sel_bg = QColor("#F2B27C") if dark else QColor("#BDD3EF")
        sel_fg = QColor("#000000")
        # Same reasoning for the *alternating-row* colour: any item view with
        # setAlternatingRowColors(True) that doesn't opt into style_item_view()
        # (or a themed objectName) falls back to the native AlternateBase palette
        # role, which Windows can tint with the system accent (e.g. dark red) –
        # that bit a second, unrelated table (the dictionary editor) after the
        # first fix only covered the widgets we had touched.  Pinning it here,
        # app-wide, closes the whole bug class instead of one widget at a time.
        s = _surfaces()
        alt_base = QColor(s["listalt"])
        base = QColor(s["control"])
        for grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                    QPalette.ColorGroup.Disabled):
            pal.setColor(grp, QPalette.ColorRole.Highlight, sel_bg)
            pal.setColor(grp, QPalette.ColorRole.HighlightedText, sel_fg)
            pal.setColor(grp, QPalette.ColorRole.AlternateBase, alt_base)
            pal.setColor(grp, QPalette.ColorRole.Base, base)
        qt_app.setPalette(pal)
        qt_app.setStyleSheet(base_qss + app_stylesheet())

    from withease.core.event_bus import bus
    bus.publish("theme.changed")
