"""System tray icon and context menu.

The tray menu is rebuilt dynamically whenever modules are enabled or disabled,
so it always stays compact and only shows what the user has activated.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr

if TYPE_CHECKING:
    from accessmate.app import AccessMateApp

ICON_PATH = Path(__file__).parent.parent.parent / "assets" / "icons" / "accessmate.ico"


class TrayIcon(QSystemTrayIcon):
    def __init__(self, app: "AccessMateApp") -> None:
        icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
        super().__init__(icon)
        self._app = app
        self.setToolTip(tr("app.name"))
        self._build_menu()

        bus.subscribe("module.started", lambda **_: self._build_menu())
        bus.subscribe("module.stopped", lambda **_: self._build_menu())
        bus.subscribe("i18n.language_changed", lambda **_: self._build_menu())

        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()

        menu.addAction(tr("tray.open_settings"), self._app.show_settings)
        menu.addSeparator()

        # Dynamic module toggles – only show enabled/known modules
        for module in self._app.get_modules():
            prefix = tr("tray.module_active_prefix") if module.enabled else "   "
            menu.addAction(f"{prefix}{module.DISPLAY_NAME}", module.toggle)

        menu.addSeparator()

        # Profile submenu
        profile_menu = menu.addMenu(tr("tray.switch_profile"))
        for profile_name in self._app.list_profiles():
            action = profile_menu.addAction(
                profile_name,
                lambda name=profile_name: self._app.switch_profile(name),
            )
            action.setCheckable(True)
            action.setChecked(profile_name == self._app.active_profile)

        menu.addSeparator()
        menu.addAction(tr("app.pause_all"), self._app.pause_all)
        menu.addSeparator()
        menu.addAction(tr("app.quit"), self._app.quit)

        self.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._app.show_settings()
