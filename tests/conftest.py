"""Shared test setup: close what a test opened.

Qt objects that outlive their test are destroyed in an unpredictable order
when the interpreter shuts down.  With enough of them alive the process
crashed AFTER pytest had already reported success – so the suite said
"216 passed" and CI still went red with exit code 1, which is the most
confusing failure mode there is.

Closing every top-level widget after each test (and stopping the overlay
coordinator's timer at the end of the session) removes the cause instead of
the symptom, for the tests here now and any added later.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(autouse=True)
def _close_what_the_test_opened():
    yield
    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            continue          # already gone – nothing to do
    app.processEvents()


@pytest.fixture(scope="session", autouse=True)
def _stop_background_timers():
    yield
    # The overlay coordinator is a singleton with a 16 ms timer.  Left running
    # it fires into half-destroyed widgets while the interpreter is shutting
    # down.
    try:
        from withease.gui.widgets.cursor_indicator import IndicatorCoordinator
        instance = IndicatorCoordinator._instance
        if instance is not None:
            instance._timer.stop()
    except Exception:
        pass
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
