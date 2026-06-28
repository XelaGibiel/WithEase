"""Main settings window.

Uses a sidebar navigation on the left and a stacked widget on the right.
Every module provides its own settings widget via get_settings_widget().
The window is fully keyboard-navigable (Tab order, keyboard shortcuts).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr, SUPPORTED_LANGUAGES
from accessmate.core import i18n as i18n_module

if TYPE_CHECKING:
    from accessmate.app import AccessMateApp


class MainWindow(QMainWindow):
    def __init__(self, app: "AccessMateApp") -> None:
        super().__init__()
        self._app = app
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumSize(800, 540)
        self.resize(900, 600)

        self._build_ui()
        self._restore_geometry()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Sidebar ----
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 16, 8, 8)
        sidebar_layout.setSpacing(4)

        logo = QLabel("AccessMate")
        logo.setObjectName("logo")
        sidebar_layout.addWidget(logo)
        sidebar_layout.addSpacing(12)

        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        sidebar_layout.addWidget(self._nav)

        sidebar_layout.addStretch()

        emergency_btn = QPushButton(f"⛔  {tr('app.emergency_stop')}")
        emergency_btn.setObjectName("emergencyButton")
        emergency_btn.clicked.connect(self._app.emergency_stop)
        sidebar_layout.addWidget(emergency_btn)

        root.addWidget(sidebar)

        # ---- Content area ----
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._populate_nav()
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        if self._nav.count() > 0:
            self._nav.setCurrentRow(0)

    def _populate_nav(self) -> None:
        general_widget = self._build_general_page()
        self._add_page(tr("settings.nav.general"), general_widget)

        for module in self._app.get_modules():
            widget = module.get_settings_widget()
            self._add_page(module.DISPLAY_NAME, widget)

        self._add_page(tr("settings.nav.profiles"), self._build_profiles_page())
        self._add_page(tr("settings.nav.actions"), self._build_actions_page())

    def _add_page(self, label: str, widget: QWidget) -> None:
        item = QListWidgetItem(label)
        item.setSizeHint(item.sizeHint().__class__(190, 36))
        self._nav.addItem(item)
        self._stack.addWidget(widget)

    # ------------------------------------------------------------------
    # Pages (placeholder content – will be fleshed out in v0.4)
    # ------------------------------------------------------------------

    def _build_general_page(self) -> QWidget:
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(24, 24, 24, 24)

        title = QLabel(f"<b>{tr('settings.general.title')}</b>")
        outer.addWidget(title)
        outer.addSpacing(16)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(12)

        # Language selector
        self._lang_combo = QComboBox()
        self._lang_combo.setMinimumWidth(160)
        current_lang = self._app._app_config.get("language", "de")
        for code, display_name in SUPPORTED_LANGUAGES.items():
            self._lang_combo.addItem(display_name, userData=code)
            if code == current_lang:
                self._lang_combo.setCurrentIndex(self._lang_combo.count() - 1)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow(tr("settings.general.language"), self._lang_combo)

        outer.addLayout(form)
        outer.addSpacing(8)

        note = QLabel("<i>More settings will follow in v0.4.</i>")
        note.setWordWrap(True)
        outer.addWidget(note)
        outer.addStretch()
        return widget

    def _on_language_changed(self, index: int) -> None:
        lang_code = self._lang_combo.itemData(index)
        i18n_module.load(lang_code)
        self._app._app_config["language"] = lang_code
        from accessmate.core import config
        config.save_app_config(self._app._app_config)
        # Rebuild the window title and nav labels to reflect the new language
        self.setWindowTitle(tr("settings.title"))

    def _build_profiles_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(QLabel(f"<b>{tr('settings.profiles.title')}</b>"))
        layout.addWidget(QLabel("(follows in v0.5)"))
        layout.addStretch()
        return widget

    def _build_actions_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(QLabel(f"<b>{tr('settings.actions.title')}</b>"))
        layout.addWidget(QLabel(tr("settings.actions.description")))
        layout.addWidget(QLabel("(follows in v0.5)"))
        layout.addStretch()
        return widget

    # ------------------------------------------------------------------
    # Window state
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        pass  # TODO: save/restore window size and position via app config

    def closeEvent(self, event) -> None:
        event.accept()
        bus.publish("gui.settings_closed")
