"""Small UI helpers shared across settings pages and dialogs."""
from __future__ import annotations

from withease.core.i18n import tr
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
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
            icon_lbl.setObjectName(title_obj)
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


def em(units: float) -> int:
    """Pixels for ``units`` line heights of the CURRENT application font.

    Use this instead of hard-coded pixel sizes wherever a widget dimension
    should grow with the user's font-size setting (accessibility): the value
    is re-evaluated on every rebuild, so the layout scales with the font.
    """
    app = QApplication.instance()
    line = QFontMetrics(app.font()).height() if app else 16
    return round(line * units)


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
