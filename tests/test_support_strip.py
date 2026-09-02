"""The one-off support note must be readable at the default window size.

It lives as a strip UNDER the pages so it never covers or interrupts anything.
But a QStackedWidget claims the LARGEST minimum height of all its pages – even
the ones not on screen – and the strip is the part that can shrink.  At the
default window size it was therefore squeezed to about half the height its
text needs: the message broke off mid sentence and its own buttons ("Später",
"Nicht mehr anzeigen") were below the window edge.  An appeal nobody can read,
with no way to say no.

Raising the window's minimum height instead would make the window unshrinkable
for as long as the note is up, which on a small laptop screen is worse.  So the
window grows once, by exactly what is missing, when there is room on screen.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "src")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from withease.core.i18n import load  # noqa: E402
from withease.gui import theme  # noqa: E402
from withease.gui.ui_utils import em  # noqa: E402


class _FakeApp:
    """Just enough of WithEaseApp for the strip to build."""

    def support_hint_state(self):
        return "pending"

    def set_support_hint_state(self, *_args, **_kwargs):
        pass

    def usage_seconds(self):
        return 10 ** 6


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    load("de")
    theme.apply_theme(instance, "dark", font_pt=10)
    return instance


def _content_area(app, height: int, pages_demand: int):
    """The window's content column: pages (stretch 1) above, strip below."""
    from withease.gui.widgets.support_hint import SupportHint

    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    pages = QLabel("Seiten")
    pages.setMinimumHeight(pages_demand)
    pages.setSizePolicy(QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Expanding)
    layout.addWidget(pages, 1)

    hint = SupportHint(_FakeApp())
    holder = QScrollArea()
    holder.setWidgetResizable(True)
    holder.setFrameShape(QScrollArea.Shape.NoFrame)
    holder.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    holder.setWidget(hint)
    holder.setSizePolicy(QSizePolicy.Policy.Preferred,
                         QSizePolicy.Policy.Maximum)
    holder.setMinimumHeight(em(5))
    holder.setMaximumHeight(hint.sizeHint().height())
    layout.addWidget(holder)

    box.resize(900, height)
    box.show()
    for _ in range(3):
        app.processEvents()
    return box, holder


def test_the_strip_is_squeezed_when_the_pages_demand_room(app):
    """The defect itself – without it the fix below proves nothing."""
    box, holder = _content_area(app, height=520, pages_demand=400)
    assert holder.height() < holder.maximumHeight()
    box.deleteLater()


def test_growing_the_window_by_the_shortfall_shows_it_completely(app):
    box, holder = _content_area(app, height=520, pages_demand=400)
    missing = holder.maximumHeight() - holder.height()
    assert missing > 0

    box.resize(box.width(), box.height() + missing)   # what the window does
    for _ in range(3):
        app.processEvents()

    assert holder.height() >= holder.maximumHeight()
    box.deleteLater()


def test_the_fitter_never_pins_it_below_what_the_text_needs(app):
    from withease.gui.main_window import _SupportStripFitter

    box, holder = _content_area(app, height=900, pages_demand=200)
    hint = holder.widget()
    fitter = _SupportStripFitter(holder, hint)
    fitter.refit()
    for _ in range(3):
        app.processEvents()

    width = holder.viewport().width() or holder.width()
    needed = (hint.heightForWidth(width) if hint.hasHeightForWidth()
              else hint.sizeHint().height())
    assert holder.maximumHeight() >= needed
    box.deleteLater()
