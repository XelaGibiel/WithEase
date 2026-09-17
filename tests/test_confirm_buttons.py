"""A red "Schließen", and a green check mark where an action is invisible.

Asked for from real use: in the dictation window the button that ends the
session should look like it, and "Kopieren" / "Kopieren & Schließen" should
confirm that they did something - text on the clipboard looks exactly like
nothing having happened.

Two details matter more than the colours:

* "Kopieren & Schließen" may close only AFTER the check mark was visible.
  On a window that hides in the same instant it would reach no one.
* The button keeps its width while it shows the shorter "✓ Kopiert".  A
  button shrinking under the pointer moves its neighbour into the spot
  where the next click was already on its way - for someone with a tremor
  that is a click on the wrong button.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from withease.gui import theme  # noqa: E402
from withease.gui.ui_utils import flash_confirmation  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# -- contrast ----------------------------------------------------------------

def _luminance(hex_colour: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("contrast", [False, True])
@pytest.mark.parametrize("fill", ["danger_fill", "ok_fill"])
def test_every_fill_is_readable_in_every_theme(monkeypatch, dark, contrast, fill):
    monkeypatch.setattr(theme, "is_dark", lambda: dark)
    monkeypatch.setattr(theme, "high_contrast", lambda: contrast)
    background, text = getattr(theme, fill)()
    assert _contrast(background, text) >= 4.5, (fill, dark, contrast)


# -- the confirmation itself -------------------------------------------------

def test_the_button_confirms_and_then_comes_back(app):
    button = QPushButton("Kopieren")
    button.show()
    done = []
    flash_confirmation(button, "✓ Kopiert", then=lambda: done.append(1), ms=50)
    assert button.text() == "✓ Kopiert"
    assert button.property("confirmed") is True
    assert done == [], "then runs after the check mark, not with it"
    QTest.qWait(150)
    assert button.text() == "Kopieren"
    assert not button.property("confirmed")
    assert done == [1]


def test_the_button_keeps_its_width_while_confirming(app):
    button = QPushButton("Kopieren && Schließen")
    button.show()
    app.processEvents()
    before = button.width()
    flash_confirmation(button, "✓ Kopiert", ms=50)
    app.processEvents()
    assert button.width() >= before, "a shrinking button moves its neighbour"
    QTest.qWait(150)
    assert button.minimumWidth() == 0, "and lets go of the width afterwards"


def test_a_second_flash_does_not_lose_the_real_label(app):
    button = QPushButton("Kopieren")
    button.show()
    flash_confirmation(button, "✓ Kopiert", ms=50)
    flash_confirmation(button, "✓ Kopiert", ms=50)     # pressed again quickly
    QTest.qWait(200)
    assert button.text() == "Kopieren"


# -- in the dictation window -------------------------------------------------

@pytest.fixture
def win(app):
    import dictation_window as dw
    copied = []
    w = dw.DictationWindow(on_insert=lambda _t: True, on_copy=copied.append)
    w.copied = copied
    w.show()
    yield w
    w.close()


def test_close_is_red(win):
    assert win._close_btn.property("dangerFill") is True


def test_copy_shows_the_check_mark(win):
    import dictation_window as dw
    win._edit.setPlainText("Straße 3")
    win._do_copy()
    assert win.copied == ["Straße 3"]
    assert win._copy_btn.text() == dw._t("win.copied")


def test_copy_and_close_waits_until_the_check_mark_was_seen(win):
    import dictation_window as dw
    win._edit.setPlainText("Straße 3")
    win._do_copy_and_close()
    assert win.copied == ["Straße 3"]
    assert win.isVisible(), "closing at once would hide the confirmation"
    assert win._copy_close_btn.text() == dw._t("win.copied")
    QTest.qWait(1100)
    assert not win.isVisible()
    assert win.text() == ""


def test_pressing_twice_copies_and_closes_once(win):
    win._edit.setPlainText("Straße 3")
    win._do_copy_and_close()
    win._do_copy_and_close()
    assert win.copied == ["Straße 3"]
    QTest.qWait(1100)
    assert not win.isVisible()


def test_nothing_to_copy_just_closes(win):
    win._edit.setPlainText("   ")
    win._do_copy_and_close()
    assert win.copied == []
    assert not win.isVisible()
