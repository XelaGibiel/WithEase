"""SupportHint – the one-off, dismissible note about supporting WithEase.

Deliberately restrained.  The people this program is written for depend on it,
and a donation prompt aimed at someone in that position is only acceptable if
it behaves like a note, not like a request:

* it appears **once**, and only after the program has actually been used for a
  while (see ``WithEaseApp.active_seconds`` – time running and not
  emergency-stopped, so a PC left on overnight does not trigger it);
* it is a strip inside the settings window, never a modal dialog and never
  anything that interrupts a task;
* all three answers are equally easy to give, and "Nicht mehr anzeigen" is a
  real, permanent answer – it is stored in app.json, so it survives updates;
* it never comes back on its own, and never mentions how much the program is
  being used.
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from withease.core.i18n import tr
from withease.gui import theme
from withease.gui.ui_utils import WrappingLabel, em

KOFI_URL = "https://ko-fi.com/xelagibiel"

# Roughly ten hours of real use.  Long enough that anyone seeing this has had
# genuine value from the program first.
SHOW_AFTER_SECONDS = 10 * 60 * 60
# "Später" does not mean next week: it pushes the note out by another long
# stretch of actual use.
POSTPONE_SECONDS = 40 * 60 * 60


# --- testing aids ----------------------------------------------------------
# Waiting ten real hours to look at a strip of UI is not a sensible test loop,
# so two environment variables shorten it.  Deliberately environment-only: no
# switch in the settings UI, nothing an end user can trip over, and nothing
# left behind in a released build unless somebody sets the variable on purpose.
#
#   WITHEASE_SUPPORT_HINT_AFTER=30   -> both thresholds become 30 seconds
#   WITHEASE_SUPPORT_HINT_FORCE=1    -> show it once per program start,
#                                       ignoring the counter and any answer
#                                       given in an EARLIER run
#
# FORCE deliberately still respects an answer given in the RUNNING session:
# ignoring it too meant the strip reappeared seconds after "Nicht mehr
# anzeigen" was clicked, which makes the button look broken and leaves it
# untestable.  Restart to see the note again.
#   WITHEASE_SUPPORT_HINT_RESET=1    -> forget the stored answer and the usage
#                                       counter at start-up, so every test run
#                                       begins from a clean slate.  Without it
#                                       one click on "Nicht mehr anzeigen"
#                                       would end the note for good and leave
#                                       nothing to test.
_ENV_AFTER = "WITHEASE_SUPPORT_HINT_AFTER"
_ENV_FORCE = "WITHEASE_SUPPORT_HINT_FORCE"
_ENV_RESET = "WITHEASE_SUPPORT_HINT_RESET"

# Set once the user has answered in THIS run (see SupportHint._answer).
_answered_this_run = False


def _override_seconds() -> int | None:
    raw = os.environ.get(_ENV_AFTER, "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        logging.warning("%s=%r is not a number - ignored", _ENV_AFTER, raw)
        return None
    return max(0, value)


def reset_requested() -> bool:
    return os.environ.get(_ENV_RESET, "").strip() not in ("", "0", "false")


def apply_test_reset(app_config: dict) -> bool:
    """Clear the stored answer + usage counter when the reset switch is set.

    Returns True when something was changed (the caller saves).  Only ever does
    anything with the environment variable present, so a normal start can never
    lose the user's real answer.
    """
    if not reset_requested():
        return False
    app_config["support_hint_state"] = "pending"
    app_config["support_hint_snoozed_at"] = 0
    app_config["active_seconds"] = 0
    logging.info("support hint: state and usage counter reset via %s",
                 _ENV_RESET)
    return True


def forced() -> bool:
    return os.environ.get(_ENV_FORCE, "").strip() not in ("", "0", "false")


def should_show(app) -> bool:
    """Whether the note is due – and whether now is a decent moment for it."""
    if app.is_paused:
        # Emergency stop is the worst possible moment for anything optional.
        return False
    if forced():
        # "Once per program start": an answer given in THIS run still counts,
        # otherwise the strip returns seconds after being dismissed and the
        # button looks broken.  Only applied to the test switch – in normal
        # operation the timing below decides, so "Später" still comes back
        # even in a session that runs for days.
        if _answered_this_run:
            return False
        logging.info("support hint: forced via %s", _ENV_FORCE)
        return True
    state = app.support_hint_state()
    if state == "done":
        return False
    override = _override_seconds()
    if override is not None:
        first, postpone = override, override
        logging.info("support hint: thresholds shortened to %ss via %s",
                     override, _ENV_AFTER)
    else:
        first, postpone = SHOW_AFTER_SECONDS, POSTPONE_SECONDS
    if state == "later":
        # Counted from the moment "Später" was pressed, not from the install.
        needed = app.support_hint_snoozed_at() + postpone
    else:
        needed = first
    return app.active_seconds() >= needed


class SupportHint(QFrame):
    """The strip itself.  Emits ``closed`` once the user has answered."""

    closed = Signal()

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self.setObjectName("card")

        # Never let the strip be squeezed below what its own text needs: in a
        # short window the layout otherwise shrinks it and the wrapped lines
        # draw straight through each other.  It takes the height it needs and
        # the pages above it keep the rest (they scroll on their own).
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Minimum)

        outer = QVBoxLayout(self)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        icon = QLabel("☕")
        icon.setObjectName("cardIcon")
        head.addWidget(icon)
        title = QLabel(tr("support.hint.title"))
        title.setObjectName("cardTitle")
        head.addWidget(title)
        head.addStretch(1)
        outer.addLayout(head)

        text = WrappingLabel(tr("support.hint.text"))
        text.setWordWrap(True)
        outer.addWidget(text)

        thanks = WrappingLabel(tr("support.hint.thanks"))
        thanks.setWordWrap(True)
        thanks.setStyleSheet(theme.hint_style())
        outer.addWidget(thanks)

        # Real air before the buttons: they are the decision, and a decision
        # crammed against the text it follows reads as pressure.  Scaled with
        # the font so it stays proportionate at every size setting.
        outer.addSpacing(em(0.9))

        row = QHBoxLayout()
        row.setSpacing(10)
        # All three answers are plain buttons of equal weight – no primary
        # styling on "Ansehen", because declining must not feel like the
        # smaller, greyer option.
        open_btn = QPushButton(tr("support.hint.open"))
        open_btn.clicked.connect(self._on_open)
        row.addWidget(open_btn)
        later_btn = QPushButton(tr("support.hint.later"))
        later_btn.clicked.connect(lambda: self._answer("later"))
        row.addWidget(later_btn)
        never_btn = QPushButton(tr("support.hint.never"))
        never_btn.clicked.connect(lambda: self._answer("done"))
        row.addWidget(never_btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.setStyleSheet(f"QFrame#card {{ border-color: {theme.accent()}; }}")

    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        QDesktopServices.openUrl(QUrl(KOFI_URL))
        self._answer("done")

    def _answer(self, state: str) -> None:
        global _answered_this_run
        _answered_this_run = True
        self._app.set_support_hint_state(state)
        self.setVisible(False)
        self.closed.emit()
        self.deleteLater()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Esc is the same as "Später" – dismissing must never be harder than
        # accepting.
        if event.key() == Qt.Key.Key_Escape:
            self._answer("later")
            return
        super().keyPressEvent(event)
