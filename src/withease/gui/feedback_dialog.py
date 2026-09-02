"""Feedback dialog – lets users send structured feedback to the author.

Submits over HTTPS to a hosted form endpoint (Formspree-style) which forwards
the message to the author's email.  No mail client on the user's machine is
needed and no secret is embedded – the endpoint only accepts POSTs that email
the author.  Uses urllib (stdlib), so it works from source and in the .exe.
"""
from __future__ import annotations

import json
import platform
import re
import threading
import urllib.error
import urllib.request
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from withease import __version__
from withease.core.i18n import tr
from withease.gui import theme

# Hosted form endpoint that emails the author (Formspree).  Empty = feedback
# not configured yet.
FEEDBACK_ENDPOINT = "https://formspree.io/f/xwvdgrob"

# Where the fallback sends people when the form is unreachable.
# Deliberately NOT an email address: one written into a public,
# distributed program is harvested and spammed within weeks.
ISSUES_URL = "https://github.com/XelaGibiel/WithEase/issues/new"

_TIMEOUT = 20

# Simple, permissive email check – only used to decide whether the optional
# contact field can be used as Formspree's reply-to address.  Formspree rejects
# the whole submission (HTTP 422) if its reserved "email" field is not a valid
# address, so anything that is not clearly an email is sent as a plain field.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _accepted(status: int, body: str) -> tuple[bool, str]:
    """Whether the server really TOOK the message – not just answered.

    The status code alone is not enough: a hosted form service answers 200 for
    a submission it merely queued (an unconfirmed form, for instance) and mails
    the owner a confirmation request instead of the message.  The app then said
    "✓ sent" while nothing ever arrived, which is the worst possible outcome:
    the user believes they have been heard.

    So the body decides where it says anything, and only a clear yes counts.
    A 2xx with a body we cannot read is still treated as success – being
    over-strict would push people to the fallback for no reason.
    """
    if not 200 <= status < 300:
        return False, f"HTTP {status}"
    try:
        answer = json.loads(body) if body.strip() else {}
    except ValueError:
        return True, ""                     # not JSON – take the 2xx at face value
    if not isinstance(answer, dict):
        return True, ""
    if answer.get("ok") is False or answer.get("error") or answer.get("errors"):
        problem = (answer.get("error")
                   or answer.get("errors") or "abgelehnt")
        return False, str(problem)[:120]
    return True, ""


class _Bridge(QObject):
    # ok, error-kind ("" | "network" | "server" | "unknown"), detail text
    finished = Signal(bool, str, str)


class FeedbackDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("feedback.title"))
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(tr("feedback.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)

        self._category = QComboBox()
        for key in ("bug", "suggestion", "question", "other"):
            self._category.addItem(tr(f"feedback.category.{key}"), key)
        form.addRow(tr("feedback.category"), self._category)

        self._message = QPlainTextEdit()
        self._message.setPlaceholderText(tr("feedback.message.placeholder"))
        self._message.setMinimumHeight(120)
        form.addRow(tr("feedback.message"), self._message)

        self._name = QLineEdit()
        form.addRow(tr("feedback.name"), self._name)

        self._contact = QLineEdit()
        self._contact.setPlaceholderText(tr("feedback.contact.placeholder"))
        form.addRow(tr("feedback.contact"), self._contact)

        layout.addLayout(form)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # Shown only when sending failed.  Without it a failed send simply
        # threw the typed message away – the one outcome that must never
        # happen, because the person has already done all the work.
        self._fallback_hint = QLabel(tr("feedback.fallback"))
        self._fallback_hint.setWordWrap(True)
        self._fallback_hint.setStyleSheet(theme.hint_style())
        layout.addWidget(self._fallback_hint)

        fallback_row = QHBoxLayout()
        self._copy_btn = QPushButton(tr("feedback.copy"))
        self._copy_btn.setMinimumHeight(theme.target_px())
        self._copy_btn.clicked.connect(self._on_copy)
        fallback_row.addWidget(self._copy_btn)
        self._issue_btn = QPushButton(tr("feedback.open_issues"))
        self._issue_btn.setMinimumHeight(theme.target_px())
        self._issue_btn.clicked.connect(self._on_open_issue)
        fallback_row.addWidget(self._issue_btn)
        fallback_row.addStretch(1)
        layout.addLayout(fallback_row)
        self._set_fallback_visible(False)

        buttons = QDialogButtonBox()
        self._send_btn = QPushButton(tr("feedback.send"))
        self._send_btn.setDefault(True)
        self._send_btn.clicked.connect(self._on_send)
        buttons.addButton(self._send_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

        self._bridge = _Bridge()
        self._bridge.finished.connect(self._on_finished)

    # ------------------------------------------------------------------

    def _on_send(self) -> None:
        message = self._message.toPlainText().strip()
        if not message:
            self._status.setText(tr("feedback.empty"))
            self._status.setStyleSheet(theme.warn_style())
            return
        if not FEEDBACK_ENDPOINT:
            self._status.setText(tr("feedback.not_configured"))
            self._status.setStyleSheet(theme.warn_style())
            return

        contact = self._contact.text().strip()
        payload: dict[str, Any] = {
            "category": self._category.currentData(),
            "message": message,
            "name": self._name.text().strip(),
            "app_version": __version__,
            "os": f"{platform.system()} {platform.release()}",
            "_subject": f"WithEase-Feedback ({self._category.currentData()})",
        }
        # The contact field is optional.  Only pass it as Formspree's reserved
        # "email" (reply-to) field when it is a valid address – otherwise the
        # whole submission is rejected with HTTP 422.  A non-email contact (e.g.
        # a phone number) is still forwarded, just under a plain field name.
        if contact:
            if _EMAIL_RE.match(contact):
                payload["email"] = contact
            else:
                payload["contact"] = contact

        self._send_btn.setEnabled(False)
        self._status.setStyleSheet(theme.hint_style())
        self._status.setText(tr("feedback.sending"))

        def run() -> None:
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    FEEDBACK_ENDPOINT, data=data, method="POST",
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json",
                             "User-Agent": "WithEase"})
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    status = resp.status
                    body = resp.read(2000).decode("utf-8", "replace")
                ok, detail = _accepted(status, body)
                self._bridge.finished.emit(ok, "" if ok else "server",
                                           detail or f"HTTP {status}")
            except urllib.error.HTTPError as exc:
                # The server answered but refused the submission (e.g. 422).
                # This is NOT a connection problem, so don't blame the network.
                self._bridge.finished.emit(False, "server", f"HTTP {exc.code}")
            except urllib.error.URLError as exc:
                # No answer at all – genuinely offline / DNS / timeout.
                self._bridge.finished.emit(False, "network", str(exc.reason)[:200])
            except Exception as exc:
                self._bridge.finished.emit(False, "unknown", str(exc)[:200])

        threading.Thread(target=run, daemon=True, name="feedback").start()

    def _on_finished(self, ok: bool, kind: str, detail: str) -> None:
        self._send_btn.setEnabled(True)
        if ok:
            self._status.setStyleSheet(f"color: {theme.ok_color()};")
            self._status.setText(tr("feedback.sent"))
            self._message.clear()
            self._set_fallback_visible(False)
            return
        self._status.setStyleSheet(theme.warn_style())
        if kind == "network":
            self._status.setText(tr("feedback.failed_network"))
        elif kind == "server":
            self._status.setText(tr("feedback.failed_server", err=detail))
        else:
            self._status.setText(tr("feedback.failed", err=detail))
        # Whatever went wrong, the typed message must not be lost.
        self._set_fallback_visible(True)

    # -- when sending fails --------------------------------------------

    def _set_fallback_visible(self, visible: bool) -> None:
        self._fallback_hint.setVisible(visible)
        self._copy_btn.setVisible(visible)
        self._issue_btn.setVisible(visible)

    def _formatted_message(self) -> str:
        """The whole submission as plain text – what the copy button hands over
        and what a bug report should contain anyway."""
        lines = [
            f"WithEase {__version__} · {platform.system()} {platform.release()}",
            f"{tr('feedback.category')}: {self._category.currentText()}",
        ]
        name = self._name.text().strip()
        contact = self._contact.text().strip()
        if name:
            lines.append(f"{tr('feedback.name')}: {name}")
        if contact:
            lines.append(f"{tr('feedback.contact')}: {contact}")
        lines.append("")
        lines.append(self._message.toPlainText().strip())
        return "\n".join(lines)

    def _on_copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._formatted_message())
            self._status.setStyleSheet(f"color: {theme.ok_color()};")
            self._status.setText(tr("feedback.copied"))

    def _on_open_issue(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(ISSUES_URL))
