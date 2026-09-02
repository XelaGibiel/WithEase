"""Feedback must never be silently lost.

Two failure modes this pins down:

* The dialog used to call any 2xx a success.  A hosted form service answers
  200 for a submission it merely QUEUED – an unconfirmed form, say – and mails
  the owner a confirmation request instead of the message.  The app then said
  "✓ sent" while nothing ever arrived: the user believes they have been heard,
  and the author never learns of the problem.

* When sending failed there was no way out at all: no copy, no alternative,
  nothing.  The typed message was simply gone – after the person had already
  done all the work of writing it.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.gui.feedback_dialog import (  # noqa: E402
    FeedbackDialog,
    _accepted,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# -- reading the answer, not just the status ----------------------------

@pytest.mark.parametrize("status, body, expected", [
    (200, '{"ok": true}', True),
    (200, '{"next": "/thanks"}', True),
    (200, "", True),
    (200, "<html>thanks</html>", True),        # not JSON – take it at face value
    (200, '{"ok": false}', False),             # answered, but did NOT take it
    (200, '{"error": "Form not found"}', False),
    (200, '{"errors": [{"message": "inactive"}]}', False),
    (422, '{"error": "invalid email"}', False),
    (429, "", False),                          # rate limited / quota gone
    (500, "", False),
])
def test_only_a_real_yes_counts_as_sent(status, body, expected):
    accepted, _detail = _accepted(status, body)
    assert accepted is expected


def test_the_reason_is_kept_for_the_user():
    _accepted_ok, detail = _accepted(200, '{"error": "Form not found"}')
    assert "Form not found" in detail


# -- the way out when it fails ------------------------------------------

def test_the_fallback_is_hidden_until_something_goes_wrong(app):
    dialog = FeedbackDialog()
    assert not dialog._copy_btn.isVisibleTo(dialog)
    assert not dialog._issue_btn.isVisibleTo(dialog)


def test_a_failure_offers_a_way_out(app):
    dialog = FeedbackDialog()
    dialog._on_finished(False, "network", "")
    assert dialog._copy_btn.isVisibleTo(dialog)
    assert dialog._issue_btn.isVisibleTo(dialog)


def test_success_hides_it_again(app):
    dialog = FeedbackDialog()
    dialog._on_finished(False, "network", "")
    dialog._on_finished(True, "", "")
    assert not dialog._copy_btn.isVisibleTo(dialog)


def test_the_copy_keeps_the_whole_submission(app):
    dialog = FeedbackDialog()
    dialog._message.setPlainText("Der Knopf reagiert nicht.")
    dialog._name.setText("Alex")
    text = dialog._formatted_message()
    assert "Der Knopf reagiert nicht." in text
    assert "Alex" in text
    assert "WithEase" in text          # version and OS, for a bug report


def test_copying_puts_it_on_the_clipboard(app):
    dialog = FeedbackDialog()
    dialog._message.setPlainText("Bitte lauter.")
    dialog._on_copy()
    assert "Bitte lauter." in QApplication.clipboard().text()
