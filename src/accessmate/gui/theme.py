"""Theme handling – light / dark / system, contrast level and font size.

This module is the single source of truth for every colour that is not taken
straight from the Qt palette.  Pages are rebuilt after a theme change, so the
style helpers below are evaluated at build time and always match the active
scheme – hint texts, warnings and selection colours stay readable in light
AND dark mode (WCAG-oriented contrast, see the accessibility notes per value).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

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
    return f"""
        {cls} {{ outline: 0; }}
        {cls}::item {{
            border: none;
            padding: 2px 4px;
        }}
        {cls}::item:selected, {cls}::item:focus {{
            background-color: {bg};
            color: {fg};
            border: none;
        }}
    """


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

    # Font size first (styles below derive sizes from it).  0 = system size.
    font = qt_app.font()
    font.setPointSize(font_pt if font_pt and font_pt > 0 else _default_font_pt)
    qt_app.setFont(font)

    scheme = _SCHEMES.get(name, Qt.ColorScheme.Unknown)
    try:
        qt_app.styleHints().setColorScheme(scheme)
    except Exception:
        pass
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
        # Strong, always-visible keyboard-focus indicator (WCAG 2.4.7/2.4.11).
        qt_app.setStyleSheet(
            base_qss +
            " QPushButton:focus, QComboBox:focus, QLineEdit:focus,"
            " QSpinBox:focus, QDoubleSpinBox:focus, QCheckBox:focus,"
            " QListWidget:focus, QTableWidget:focus, QSlider:focus"
            f" {{ border: 2px solid {accent()}; }}")
    else:
        qt_app.setPalette(QPalette())   # back to the scheme's own palette
        qt_app.setStyleSheet(base_qss)
        try:
            qt_app.styleHints().setColorScheme(scheme)
        except Exception:
            pass

    from accessmate.core.event_bus import bus
    bus.publish("theme.changed")
