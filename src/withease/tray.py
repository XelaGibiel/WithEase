"""System tray icon and context menu.

The tray menu is rebuilt dynamically whenever modules are enabled or disabled,
so it always stays compact and only shows what the user has activated.

Icon colors:
- Blue  (#1565C0) – normal, at least one module active
- Grey  (#616161) – running but no modules active
- Red   (#C62828) – paused (emergency stop or pause-all)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.core.resources import app_icon_path

if TYPE_CHECKING:
    from withease.app import WithEaseApp

ICON_PATH = app_icon_path()


def _make_icon(color: str) -> QIcon:
    """Generate an 'AM' tray icon in the given hex color."""
    px = QPixmap(32, 32)
    px.fill(QColor(color))
    painter = QPainter(px)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(13)
    painter.setFont(font)
    painter.drawText(
        px.rect(),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        "AM",
    )
    painter.end()
    return QIcon(px)


class TrayIcon(QSystemTrayIcon):
    # Icons are created lazily inside __init__ so QApplication already exists.
    _icon_active: QIcon | None = None
    _icon_idle:   QIcon | None = None
    _icon_paused: QIcon | None = None

    def __init__(self, app: "WithEaseApp") -> None:
        # Build icons now – QApplication is guaranteed to exist at this point.
        if TrayIcon._icon_active is None:
            TrayIcon._icon_active = _make_icon("#1565C0")  # blue
            TrayIcon._icon_idle   = _make_icon("#616161")  # grey
            TrayIcon._icon_paused = _make_icon("#C62828")  # red

        icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else TrayIcon._icon_idle
        super().__init__(icon)
        self._app = app
        self._build_menu()
        self._update_tooltip()

        # A single left click opens the menu, a double click opens the
        # settings.  Windows sends the first Trigger BEFORE the DoubleClick,
        # so a short timer holds the single-click action back long enough to
        # see whether a second click turns it into a double click.
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(250)
        self._click_timer.timeout.connect(self._show_menu_at_cursor)

        bus.subscribe("module.started",          lambda **_: self._refresh())
        bus.subscribe("module.stopped",          lambda **_: self._refresh())
        bus.subscribe("app.paused",              lambda **_: self._set_paused(True))
        bus.subscribe("app.resumed",             lambda **_: self._set_paused(False))
        bus.subscribe("i18n.language_changed",   lambda **_: self._refresh())
        bus.subscribe("profiles.changed",        lambda **_: self._on_profile_changed())
        # A module whose tray contribution changed (another microphone was
        # picked) asks for the tooltip and menu to be rebuilt.
        bus.subscribe("tray.refresh",            lambda **_: self._refresh())

        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    # Icon state
    # ------------------------------------------------------------------

    def _set_paused(self, paused: bool) -> None:
        if paused:
            self.setIcon(TrayIcon._icon_paused)
        else:
            self._update_icon()
        self._update_tooltip()
        self._build_menu()

    def _on_profile_changed(self) -> None:
        self._update_tooltip()
        self._build_menu()

    # Windows keeps only the first 127 characters of a tray tooltip.
    _TOOLTIP_LIMIT = 127

    def _update_tooltip(self) -> None:
        """Show app name, active profile and running state at a glance, so
        hovering the tray icon tells the user immediately whether WithEase
        is active and which profile is in use.

        Modules may add a line of their own - the dictation module names the
        microphone it records from.  The tray publishes "tray.tooltip" with a
        list and shows what was appended; the core itself knows nothing
        about microphones."""
        state = (tr("tray.state.paused") if self._app.is_paused
                 else tr("tray.state.active"))
        lines = [f"{tr('app.name')} – {state}",
                 tr("tray.tooltip.profile", name=self._app.active_profile)]
        extra: list[str] = []
        try:
            bus.publish("tray.tooltip", lines=extra)
        except Exception:
            extra = []
        text = "\n".join(lines)
        for line in (str(x) for x in extra if x):
            # A line that would run past the Windows limit is left out whole
            # rather than cut off in the middle of a word.
            if len(text) + 1 + len(line) > self._TOOLTIP_LIMIT:
                break
            text += "\n" + line
        self.setToolTip(text)

    def _update_icon(self) -> None:
        if ICON_PATH.exists():
            self.setIcon(QIcon(str(ICON_PATH)))
            return
        any_active = any(m.enabled for m in self._app.get_modules())
        self.setIcon(TrayIcon._icon_active if any_active else TrayIcon._icon_idle)

    def _refresh(self) -> None:
        self._update_icon()
        self._update_tooltip()
        self._build_menu()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()

        menu.addAction(tr("tray.open_settings"), self._app.show_settings)
        menu.addSeparator()

        for module in self._app.get_modules():
            prefix = tr("tray.module_active_prefix") if module.enabled else "   "
            menu.addAction(f"{prefix}{module.DISPLAY_NAME}", module.toggle)

        # Sections contributed by modules - the dictation module's microphone
        # list, for one - so a quick switch does not mean opening the
        # settings.  Each section refills itself whenever it is opened, so a
        # microphone plugged in after start-up is listed without a restart.
        sections: list[dict] = []
        try:
            bus.publish("tray.menu", sections=sections)
        except Exception:
            sections = []
        if sections:
            menu.addSeparator()
            for section in sections:
                self._add_section(menu, section)

        menu.addSeparator()

        profile_menu = menu.addMenu(tr("tray.switch_profile"))
        for profile_name in self._app.list_profiles():
            action = profile_menu.addAction(
                profile_name,
                lambda name=profile_name: self._app.switch_profile(name),
            )
            action.setCheckable(True)
            action.setChecked(profile_name == self._app.active_profile)

        menu.addSeparator()

        if self._app._paused:
            menu.addAction(tr("app.resume_all"), self._app.resume_all)
        else:
            menu.addAction(tr("app.pause_all"), self._app.pause_all)

        menu.addSeparator()
        menu.addAction(tr("app.quit"), self._app.quit)

        self.setContextMenu(menu)

    def _add_section(self, menu: QMenu, section: dict) -> None:
        """One submenu from a module's ``{"title": ..., "populate": ...}``.

        ``populate`` returns ``(label, checked, callback)`` tuples."""
        title = str(section.get("title") or "")
        populate = section.get("populate")
        if not title or not callable(populate):
            return
        sub = menu.addMenu(title)
        self._fill_section(sub, populate)
        sub.aboutToShow.connect(
            lambda sub=sub, populate=populate: self._fill_section(sub, populate))

    @staticmethod
    def _fill_section(sub: QMenu, populate) -> None:
        sub.clear()
        try:
            items = list(populate() or [])
        except Exception:
            items = []
        for label, checked, callback in items:
            action = sub.addAction(str(label), callback)
            action.setCheckable(True)
            action.setChecked(bool(checked))

    def _show_menu_at_cursor(self) -> None:
        menu = self.contextMenu()
        if menu is not None:
            menu.popup(QCursor.pos())

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        Reason = QSystemTrayIcon.ActivationReason
        if reason == Reason.Trigger:
            # Single left click → open the menu, but wait briefly in case a
            # second click makes it a double click (handled below).
            self._click_timer.start()
        elif reason == Reason.DoubleClick:
            self._click_timer.stop()   # cancel the pending single-click menu
            self._app.show_settings()
