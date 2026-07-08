"""Update dialog – shows release details and performs the self-update."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from accessmate import __version__
from accessmate.core import updater
from accessmate.core.i18n import tr
from accessmate.gui import theme


class _UpdateBridge(QObject):
    finished = Signal(bool, str)   # ok, error text


class UpdateDialog(QDialog):
    """Details of the available release + one-click update.

    Accessibility: plain language, large hit targets, full keyboard
    operation (Tab/Enter/Esc), no time limits.
    """

    def __init__(self, info: updater.ReleaseInfo, parent=None) -> None:
        super().__init__(parent)
        self._info = info
        self.setWindowTitle(tr("app.update.title"))
        self.setMinimumSize(460, 360)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        heading = QLabel(tr("app.update.heading", version=info.version))
        heading.setStyleSheet(theme.title_style())
        heading.setWordWrap(True)
        layout.addWidget(heading)

        versions = QLabel(tr("app.update.versions",
                             current=__version__, latest=info.version))
        versions.setWordWrap(True)
        layout.addWidget(versions)

        notes_label = QLabel(tr("app.update.notes"))
        layout.addWidget(notes_label)

        notes = QPlainTextEdit(info.notes or tr("app.update.no_notes"))
        notes.setReadOnly(True)
        notes.setAccessibleName(tr("app.update.notes"))
        layout.addWidget(notes, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        self._install_btn = QPushButton(tr("app.update.install"))
        self._install_btn.setDefault(True)
        self._install_btn.clicked.connect(self._on_install)
        btn_row.addWidget(self._install_btn)

        page_btn = QPushButton(tr("app.update.open_page"))
        page_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(info.html_url)))
        btn_row.addWidget(page_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._bridge = _UpdateBridge()
        self._bridge.finished.connect(self._on_finished)

    # ------------------------------------------------------------------

    def _on_install(self) -> None:
        self._install_btn.setEnabled(False)
        self._status.setText(tr("app.update.installing"))

        def run() -> None:
            try:
                updater.perform_update(self._info)
                self._bridge.finished.emit(True, "")
            except Exception as exc:
                self._bridge.finished.emit(False, str(exc)[:300])

        threading.Thread(target=run, daemon=True, name="update").start()

    def _on_finished(self, ok: bool, err: str) -> None:
        if ok:
            self._status.setText("")
            QMessageBox.information(self, tr("app.update.title"),
                                    tr("app.update.done"))
            updater.restart_app()
        else:
            self._install_btn.setEnabled(True)
            self._status.setText(
                tr("app.update.failed", err=err))
            self._status.setStyleSheet(theme.warn_style())
