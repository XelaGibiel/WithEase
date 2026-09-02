"""Every control a user has to hit must be big enough to hit.

WithEase is for people with limited fine motor control, so a small control is
not a cosmetic detail – it is the difference between using a feature and not
using it.  theme.target_px() defines the floor (44px, WCAG 2.5.5 AAA, growing
with the font-size setting), but nothing enforced it: a control added with a
default height simply came out small, and nobody noticed.  This test walks the
real settings pages and fails when one falls below the floor.

Deliberately checked at several font sizes: the floor grows with the font, and
a control with a hard-coded pixel height passes at 9pt and fails at 16pt.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QPushButton,
)
from PySide6.QtGui import QFont  # noqa: E402

from withease.gui import theme  # noqa: E402
from withease.gui.ui_utils import inside_click_target  # noqa: E402

# Controls whose size is set explicitly on purpose and must NOT be stretched:
# the ✕ / ▲ / ▼ / colour squares next to a field.  compact_fields() already
# skips them for the same reason (min == max width means "I chose this size").
_INTENTIONALLY_SMALL = {"✕", "×", "▲", "▼", "◀", "▸", "＋", "+"}

_INTERACTIVE = (QPushButton, QComboBox, QAbstractSpinBox, QCheckBox)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pages():
    """One instance of every settings page that can be built without a
    running app object."""
    from withease.modules.keyboard import KeyboardModule
    from withease.modules.macros import MacrosModule
    from withease.modules.mouse import MouseModule
    from withease.gui.settings.keyboard_settings import KeyboardSettingsWidget
    from withease.gui.settings.macros_settings import MacrosSettingsWidget
    from withease.gui.settings.mouse_settings import MouseSettingsWidget

    keyboard = KeyboardModule()
    # Open the collapsible cards, otherwise their controls are never laid out
    # and the test would silently check almost nothing.
    keyboard._settings.update(delay_enabled=True, sticky_enabled=True,
                              no_repeat_enabled=True)
    mouse = MouseModule()
    mouse._settings.update(centering_enabled=True, precision_enabled=True,
                           click_lock_enabled=True, highlight_enabled=True)
    return {
        "Tastatur": KeyboardSettingsWidget(keyboard),
        "Maus": MouseSettingsWidget(mouse),
        "Makros": MacrosSettingsWidget(MacrosModule()),
    }


def _too_small(page, floor):
    """(name, height) of every visible control shorter than the floor."""
    from withease.gui.ui_utils import compact_fields

    page.resize(1000, 1600)
    compact_fields(page)
    page.show()
    QApplication.processEvents()

    bad = []
    widgets = []
    for cls in _INTERACTIVE:          # findChildren takes one type at a time
        widgets.extend(page.findChildren(cls))
    for widget in widgets:
        if not widget.isVisible():
            continue
        text = (widget.text() if hasattr(widget, "text") else "") or ""
        if text.strip() in _INTENTIONALLY_SMALL:
            continue
        if widget.minimumWidth() and widget.minimumWidth() == widget.maximumWidth():
            continue                       # explicitly fixed size
        if inside_click_target(widget):
            continue                       # a header/cell around it IS the target
        if widget.height() < floor:
            bad.append((f"{type(widget).__name__} {text.strip()!r}",
                        widget.height()))
    return bad


@pytest.mark.parametrize("point_size", [9, 10, 12, 16])
def test_controls_meet_the_click_target_floor(app, point_size):
    # Through apply_theme, never a bare setFont: the stylesheet's min-heights
    # are generated FROM the font size, so setting the font afterwards leaves
    # the controls sized for the old one and the test measures its own mistake.
    theme.apply_theme(app, "dark", font_pt=point_size)
    floor = theme.target_px()
    problems = {}
    for name, page in _pages().items():
        bad = _too_small(page, floor)
        if bad:
            problems[name] = bad
    assert not problems, (
        f"at {point_size}pt the click-target floor is {floor}px; too small: "
        + "; ".join(f"{page}: {items}" for page, items in problems.items()))


def test_the_floor_itself_grows_with_the_font(app):
    """The floor is only useful if it actually tracks the font size – a fixed
    44px would leave someone on a 20pt font with targets that are relatively
    tiny."""
    sizes = []
    for point_size in (9, 12, 16, 20):
        app.setFont(QFont("Segoe UI", point_size))
        sizes.append(theme.target_px())
    theme.apply_theme(app, "dark", font_pt=10)   # don't leave 20pt behind
    assert sizes[0] >= 44                      # WCAG AAA floor holds
    assert sizes == sorted(sizes)              # never shrinks
    assert sizes[-1] > sizes[0]                # and does grow
