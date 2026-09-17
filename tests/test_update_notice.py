"""A new version must announce itself, once, and be readable when it does.

Two things this pins down:

* The only sign of an update used to be a highlighted button in the footer.
  Someone who never looks down there never learns that a fix for their own
  problem exists - and the button says nothing about what changed anyway.
  The notes now open by themselves the first time a version is seen, and the
  version is remembered so it never becomes a nag.

* The notes are Markdown.  Shown as plain text they were the raw "##" and
  "*" characters: a wall of syntax in front of the one thing the reader
  wants to know, namely whether this update helps them.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextBrowser, QWidget  # noqa: E402

from withease.core import updater  # noqa: E402
from withease.gui.main_window import MainWindow  # noqa: E402
from withease.gui.update_dialog import UpdateDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _release(version="9.9.9", notes="## Besser treffen\n\n* Ein Punkt\n"):
    return updater.ReleaseInfo(
        version=version, notes=notes,
        html_url="https://example.invalid/release",
        zipball_url="https://example.invalid/zip")


class _Window(QWidget):
    """Just enough of MainWindow to exercise the announcement rule.

    A real QWidget, because the dialog is parented to it - but never shown,
    so ``isVisible`` is answered here instead."""

    def __init__(self, visible=True):
        super().__init__()
        self._visible = visible

    def isVisible(self):
        return self._visible

    _announce_update = MainWindow._announce_update


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Never touch the real %APPDATA%/WithEase during a test."""
    import withease.core.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(cfg, "APP_CONFIG_FILE", tmp_path / "app.json")
    return cfg


@pytest.fixture
def opened(monkeypatch):
    """Collect the announcement windows instead of putting them on screen."""
    seen = []
    monkeypatch.setattr(UpdateDialog, "show", lambda self: seen.append(self))
    return seen


def test_notes_are_shown_unprompted(app, isolated_config, opened):
    window = _Window()
    window._announce_update(_release())

    assert len(opened) == 1, "a new version must announce itself"
    assert isolated_config.load_app_config()["update_notes_seen"] == "9.9.9"


def test_the_same_version_is_announced_only_once(app, isolated_config, opened):
    window = _Window()
    window._announce_update(_release())
    window._announce_update(_release())            # same version again
    window._announce_update(_release("9.9.10"))    # a later one

    assert len(opened) == 2, "one window per version, not per check"


def test_nothing_pops_up_over_a_hidden_window(app, isolated_config, opened):
    _Window(visible=False)._announce_update(_release())

    assert opened == []
    # And it must not count as seen, or the announcement is lost for good.
    assert isolated_config.load_app_config()["update_notes_seen"] == ""


def test_release_notes_render_as_markdown(app):
    dialog = UpdateDialog(_release(notes="## Besser treffen\n\n* Ein Punkt\n"))
    browser = dialog.findChild(QTextBrowser)
    assert browser is not None
    text = browser.toPlainText()
    assert "Besser treffen" in text and "Ein Punkt" in text
    assert "##" not in text, "Markdown must be rendered, not shown as syntax"
    dialog.close()


def test_empty_notes_still_say_something(app):
    dialog = UpdateDialog(_release(notes=""))
    browser = dialog.findChild(QTextBrowser)
    assert browser.toPlainText().strip(), "an empty body must not be a blank box"
    dialog.close()
