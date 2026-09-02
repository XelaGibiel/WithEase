"""Main settings window.

Uses a sidebar navigation on the left and a stacked widget on the right.
Every module provides its own settings widget via get_settings_widget().
The window is fully keyboard-navigable (Tab order, keyboard shortcuts).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (QEasingCurve, QEvent, QObject,
                            QPropertyAnimation, Qt, QTimer)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from withease import __version__
from withease.core.event_bus import bus
from withease.core.i18n import tr, SUPPORTED_LANGUAGES
from withease.core import i18n as i18n_module
from withease.gui import theme
from withease.gui.ui_utils import mark_danger
from withease.gui.widgets.support_hint import (
    forced as forced_support_hint,
)

if TYPE_CHECKING:
    from withease.app import WithEaseApp


class _SaveToast(QLabel):
    """Small fading notification in the window's top-right corner."""

    _HOLD_MS = 3500
    _FADE_IN_MS = 200
    _FADE_OUT_MS = 600

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background-color: rgba(46, 125, 50, 230); color: white;"
            "border-radius: 6px; padding: 6px 12px;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._faded_connected = False   # is finished→_on_faded currently wired?

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(self._HOLD_MS)
        self._hold_timer.timeout.connect(self._fade_out)

    def show_message(self, text: str) -> None:
        was_visible = self.isVisible()
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move(parent.width() - self.width() - 16, 12)
        self.raise_()
        if not was_visible:
            self._effect.setOpacity(0.0)
        self.show()

        self._anim.stop()
        self._disconnect_faded()
        self._anim.setDuration(self._FADE_IN_MS)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hold_timer.start()  # restart hold on every save

    def _disconnect_faded(self) -> None:
        """Disconnect finished→_on_faded only if it is actually connected.

        A blanket ``signal.disconnect()`` on an unconnected signal emits a
        libpyside RuntimeWarning, so we track the connection instead."""
        if self._faded_connected:
            self._anim.finished.disconnect(self._on_faded)
            self._faded_connected = False

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(self._FADE_OUT_MS)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._disconnect_faded()
        self._anim.finished.connect(self._on_faded)
        self._faded_connected = True
        self._anim.start()

    def _on_faded(self) -> None:
        self._disconnect_faded()
        if self._effect.opacity() < 0.05:
            self.hide()


class _FavouriteCell(QWidget):
    """Table cell holding the favourite checkbox – the whole cell toggles it."""

    def __init__(self, checkbox: QCheckBox) -> None:
        super().__init__()
        self._checkbox = checkbox
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(checkbox)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The CELL is the target, not the box (see the caller's comment):
        # stretching the box would push its indicator off-centre again.
        self.setProperty("clickTarget", True)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._checkbox.toggle()
        event.accept()


class _SupportStripFitter(QObject):
    """Keeps the support strip exactly as tall as its wrapped text needs.

    A QScrollArea reports a tiny size hint of its own, so the strip's height
    has to be pinned with setMaximumHeight – but the right value only exists
    once the strip has a real width, because the text wraps.  Pinned at build
    time it came out too small and the note was cut off mid sentence.
    """

    def __init__(self, holder, hint) -> None:
        super().__init__(holder)
        self._holder = holder
        self._hint = hint

    def refit(self) -> None:
        import shiboken6
        if not (shiboken6.isValid(self._holder) and shiboken6.isValid(self._hint)):
            return
        width = self._holder.viewport().width() or self._holder.width()
        if width <= 0:
            return
        needed = self._hint.heightForWidth(width) if (
            self._hint.hasHeightForWidth()) else self._hint.sizeHint().height()
        needed = max(needed, self._hint.sizeHint().height())
        if needed != self._holder.maximumHeight():
            self._holder.setMaximumHeight(needed)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Resize:
            self.refit()
        return False


class MainWindow(QMainWindow):
    # Always resolves to the newest published release – the link to share.
    RELEASE_URL = "https://github.com/XelaGibiel/WithEase/releases/latest"

    def __init__(self, app: "WithEaseApp") -> None:
        super().__init__()
        self._app = app
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumSize(800, 540)
        self.resize(900, 600)

        self._build_ui()
        self._restore_geometry()
        self._save_toast = _SaveToast(self)
        bus.subscribe("profiles.changed", self._on_profiles_changed)
        bus.subscribe("profile.saved", self._on_profile_saved)
        # Styles (hint/warn/selection colours) are baked in at build time, so
        # every page must be rebuilt when light/dark/contrast changes –
        # otherwise lists keep the colours of the previous scheme.  Deferred:
        # the theme combo that triggered this lives in a page being replaced.
        bus.subscribe("theme.changed",
                      lambda **_: QTimer.singleShot(0, self._rebuild_ui))

    def _on_profile_saved(self, name: str, **_: object) -> None:
        if self.isVisible() and not getattr(self, "_rebuilding", False):
            self._save_toast.show_message(
                tr("settings.profile_saved", name=name))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        outer.addLayout(root, 1)

        # ---- Sidebar ----
        from withease.gui.ui_utils import em
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        self._sidebar = sidebar
        # ~25% narrower: the nav labels are short, and the width it used to
        # reserve was taken away from the settings cards (which then had to
        # scroll horizontally on a smaller window).
        sidebar.setFixedWidth(max(170, em(10)))
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 18, 12, 12)
        sidebar_layout.setSpacing(4)

        # Logo row: existing WithEase app icon + wordmark (both unchanged).
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(4, 0, 4, 0)
        logo_row.setSpacing(8)
        logo_icon = QLabel()
        _logo_pm = self._logo_pixmap(em(1.7))
        if _logo_pm is not None:
            logo_icon.setPixmap(_logo_pm)
        logo_row.addWidget(logo_icon)
        logo = QLabel("WithEase")
        logo.setObjectName("logo")
        logo_row.addWidget(logo)
        logo_row.addStretch()
        sidebar_layout.addLayout(logo_row)
        sidebar_layout.addSpacing(12)

        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Sized to its content when it fits; a vertical scrollbar appears only
        # when the sidebar is too short (small window + large font) so the
        # bottom entries always stay reachable.
        self._nav.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        from PySide6.QtWidgets import QSizePolicy
        # Fill the space between the logo and the emergency button: extra space
        # below the entries is just sidebar background (the list has no border),
        # and when the sidebar is too short the list shrinks and scrolls instead
        # of clipping the bottom entries or the emergency button.
        self._nav.setSizePolicy(QSizePolicy.Policy.Preferred,
                                QSizePolicy.Policy.Expanding)
        # Nav styling lives centrally in theme.app_stylesheet() (QListWidget#nav)
        # so it refreshes on every theme change; just keep it on the app font.
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app is not None:
            self._nav.setFont(_app.font())     # follow the font-size setting
        sidebar_layout.addWidget(self._nav, 1)

        self._emergency_btn = QPushButton()
        self._emergency_btn.setObjectName("emergencyButton")
        self._emergency_btn.clicked.connect(self._app.toggle_emergency)
        sidebar_layout.addWidget(self._emergency_btn)
        self._update_emergency_btn()
        # Reflect pause state changes from any source (key, tray, button)
        # on both the sidebar button and the colour-coded footer state.
        bus.subscribe("app.paused", lambda **_: self._on_pause_state_changed())
        bus.subscribe("app.resumed", lambda **_: self._on_pause_state_changed())

        root.addWidget(sidebar)

        # ---- Content area: search bar above the pages ----
        from withease.gui.settings_search import SettingsSearchBar
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self._search_bar = SettingsSearchBar(self)
        content_layout.addWidget(self._search_bar)
        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack, 1)

        # One-off support note – a strip UNDER the pages, so it never covers
        # or interrupts anything (see widgets/support_hint.py for the rules).
        self._content_layout = content_layout
        self._install_support_hint()
        # The threshold can be reached WHILE the window is open, and that is
        # not a rare case for a window people leave open.  Re-checking on a
        # slow timer means the note appears when it is due instead of only at
        # the next window build.
        self._support_timer = QTimer(self)
        # Matches the usage clock's own tick: with a shortened test threshold
        # a 15s re-check would sit idle long after the moment it is waiting
        # for (see gui/widgets/support_hint.py).
        from withease.gui.widgets.support_hint import _override_seconds
        _ov = _override_seconds()
        self._support_timer.setInterval(
            3000 if (_ov is not None and _ov < 300) else 15000)
        self._support_timer.timeout.connect(self._install_support_hint)
        self._support_timer.start()
        # The hit list floats over the pages instead of pushing them down.
        self._search_bar.set_overlay_host(content)
        root.addWidget(content, 1)

        # ---- Footer: active profile (left), version/update (right) ----
        footer_frame = QWidget()
        footer_frame.setObjectName("footer")
        footer = QHBoxLayout(footer_frame)
        footer.setContentsMargins(16, 8, 16, 8)

        self._footer_profile = QLabel()
        self._footer_profile.setStyleSheet(theme.hint_style())
        footer.addWidget(self._footer_profile)
        footer.addStretch()

        # Shows just the version when up to date; becomes a highlighted
        # button once a newer release is published.
        self._version_btn = QPushButton(
            tr("app.update.version", version=__version__))
        self._version_btn.setFlat(True)
        self._version_btn.setEnabled(False)
        self._version_btn.clicked.connect(self._on_version_clicked)
        footer.addWidget(self._version_btn)
        outer.addWidget(footer_frame)

        self._latest_release = None
        self._start_update_check()
        self._update_footer_profile()

        self._populate_nav()
        from withease.gui.ui_utils import compact_fields
        compact_fields(self._stack)
        self._refresh_search_index()
        self._nav.currentItemChanged.connect(self._on_nav_changed)
        self._select_nav_row(0)

    def _refresh_search_index(self) -> None:
        """Re-index every page after a (re)build – covers add-on modules too."""
        bar = getattr(self, "_search_bar", None)
        if bar is None:
            return
        from withease.gui.settings_search import build_index
        names: dict[int, str] = {}
        for i in range(self._nav.count()):
            item = self._nav.item(i)
            page = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(page, int):
                names[page] = item.text()
        bar.clear_query()
        bar.set_entries(build_index(self._stack, names))

    def _install_support_hint(self) -> None:
        """Add the one-off support note when it is due (and not already up)."""
        from withease.gui.widgets.support_hint import SupportHint, should_show
        if getattr(self, "_support_holder", None) is not None:
            return
        if not should_show(self._app):
            return
        hint = SupportHint(self._app)
        # In a scroll holder, NOT straight into the layout: as a fixed part
        # of the window the strip nearly doubled the window's minimum
        # height (485 -> 903px at 16pt), which on a small laptop screen
        # would put its own buttons out of reach.  With a low minimum the
        # window stays as free as before; the strip still takes its full
        # natural height whenever there is room, and only scrolls when
        # there genuinely is not.
        from PySide6.QtWidgets import QScrollArea, QSizePolicy
        from withease.gui.ui_utils import em as _em
        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QScrollArea.Shape.NoFrame)
        holder.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder.setWidget(hint)
        holder.setSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Maximum)
        holder.setMinimumHeight(_em(5))
        holder.setMaximumHeight(hint.sizeHint().height())
        hint.closed.connect(holder.deleteLater)
        self._content_layout.addWidget(holder)
        self._support_holder = holder
        hint.closed.connect(self._forget_support_hint)
        # The height above is a first guess made BEFORE the strip has a width,
        # and the text wraps – so it is refitted once the layout has run, and
        # again on every resize.  Without that the note was cut off mid
        # sentence at the default window size, with its own buttons out of
        # view: an appeal nobody can read, and no way to say "no thanks".
        self._support_fitter = _SupportStripFitter(holder, hint)
        holder.installEventFilter(self._support_fitter)
        QTimer.singleShot(0, lambda: self._fit_support_hint(holder, hint))

    def _fit_support_hint(self, holder, hint) -> None:
        """Give the strip the height it needs – growing the WINDOW if that is
        what it takes.

        Raising the window's minimum height instead would make the whole
        window unshrinkable for as long as the note is up, and on a small
        laptop screen that is worse than a note one has to scroll.  Growing
        once, only when there is room on screen, fixes the default case and
        leaves the window as free as before."""
        import shiboken6
        if not (shiboken6.isValid(holder) and shiboken6.isValid(hint)):
            return
        self._support_fitter.refit()
        missing = holder.maximumHeight() - holder.height()
        if missing <= 0:
            return
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        if available is None:
            return
        room = available.height() - self.frameGeometry().height()
        if room <= 0:
            return                       # already as tall as the screen allows
        self.resize(self.width(), self.height() + min(missing, room))

    def _forget_support_hint(self) -> None:
        self._support_holder = None
        timer = getattr(self, "_support_timer", None)
        if timer is None or forced_support_hint():
            return
        # Only "Nicht mehr anzeigen" (and "Ansehen") end it.  After "Später"
        # the timer has to keep running – postponing means it comes back, and
        # stopping the check here is exactly what stopped it coming back.
        if self._app.support_hint_state() == "done":
            timer.stop()

    def _on_nav_changed(self, current, _previous) -> None:
        if current is None:
            return
        page_index = current.data(Qt.ItemDataRole.UserRole)
        if page_index is not None:
            self._stack.setCurrentIndex(page_index)
            # A dropdown can only be measured reliably once its page has been
            # laid out; a page never shown yet has not been.  Only ever widens,
            # so repeating it on each visit is harmless.
            from withease.gui.ui_utils import align_form_labels, fix_combo_widths
            page = self._stack.currentWidget()

            def _tidy(p=page) -> None:
                fix_combo_widths(p)
                align_form_labels(p)   # one caption column for all its cards

            QTimer.singleShot(0, _tidy)

    def goto_module_page(self, module_id: str) -> bool:
        """Show the settings page of the module with this id.

        Used when a module asks to send the user where its problem can be
        fixed (see WithEaseApp.show_settings)."""
        for module in self._app.get_modules():
            if getattr(module, "MODULE_ID", "") != module_id:
                continue
            name = getattr(module, "DISPLAY_NAME", "")
            for i in range(self._nav.count()):
                item = self._nav.item(i)
                if item is not None and item.text() == name:
                    self._select_nav_row(i)
                    return True
        return False

    def _goto_page(self, page_index: int) -> None:
        """Navigate the sidebar to the entry showing the given stack page."""
        for i in range(self._nav.count()):
            item = self._nav.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == page_index:
                self._nav.setCurrentItem(item)
                return

    def _select_nav_row(self, row: int) -> None:
        """Select the given row, skipping separators (non-selectable items)."""
        for i in list(range(row, self._nav.count())) + list(range(row)):
            item = self._nav.item(i)
            if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self._nav.setCurrentRow(i)
                return

    def _update_footer_profile(self) -> None:
        """Active profile with a colour-coded state dot: green while running,
        red while paused (emergency stop) – a second, at-a-glance signal of
        whether WithEase is currently active."""
        paused = self._app.is_paused
        dot, colour, state = (
            ("●", theme.danger_color(), tr("footer.state.paused")) if paused
            else ("●", theme.ok_color(), tr("footer.state.active")))
        self._footer_profile.setText(
            f"{dot}  {tr('footer.profile', name=self._app.active_profile)}"
            f"  ·  {state}")
        self._footer_profile.setStyleSheet(
            f"color: {colour}; font-weight: bold;")

    # -- Update check ----------------------------------------------------

    def _start_update_check(self) -> None:
        """Ask GitHub for a newer release (background, silent on failure)."""
        from PySide6.QtCore import QObject, Signal

        class _Bridge(QObject):
            result = Signal(object)

        self._update_bridge = _Bridge()
        self._update_bridge.result.connect(self._on_update_check_done)
        from withease.core import updater
        updater.check_async(self._update_bridge.result.emit)

    def _on_update_check_done(self, info) -> None:
        self._latest_release = info
        if info is None:
            return  # up to date (or offline) – keep the plain version label
        self._version_btn.setText(
            tr("app.update.available", version=info.version))
        self._version_btn.setFlat(False)
        self._version_btn.setEnabled(True)
        self._version_btn.setStyleSheet(
            f"font-weight: bold; color: {theme.accent()};")

    def _on_version_clicked(self) -> None:
        if self._latest_release is None:
            return
        from withease.gui.update_dialog import UpdateDialog
        UpdateDialog(self._latest_release, self).exec()

    def _on_pause_state_changed(self) -> None:
        self._update_emergency_btn()
        if hasattr(self, "_footer_profile"):
            self._update_footer_profile()

    def _update_emergency_btn(self) -> None:
        """Show the current emergency state on the sidebar toggle button."""
        if self._app.is_paused:
            self._emergency_btn.setText(f"▶  {tr('app.resume_all')}")
            self._emergency_btn.setStyleSheet(
                "background-color: #2E7D32; color: white; font-weight: bold;")
        else:
            self._emergency_btn.setText(f"⛔  {tr('app.emergency_stop')}")
            self._emergency_btn.setStyleSheet("")

    def _populate_nav(self) -> None:
        # module_id → stack page index, for jumping there from other pages.
        self._module_pages: dict[str, int] = {"general": 0}

        general_widget = self._build_general_page()
        self._add_page(tr("settings.nav.general"), general_widget)

        # Core modules first …
        externals = []
        for module in self._app.get_modules():
            # Add-on modules (shipped-but-optional like Dictation) and external
            # third-party modules are grouped below the divider.
            if hasattr(module, "MANIFEST") or getattr(module, "IS_EXTRA", False):
                externals.append(module)
                continue
            widget = module.get_settings_widget()
            self._module_pages[module.MODULE_ID] = self._stack.count()
            self._add_page(module.DISPLAY_NAME, widget)

        self._add_page(tr("settings.nav.profiles"), self._build_profiles_page())
        self._add_page(tr("settings.nav.actions"), self._build_actions_page())

        from withease.gui.settings.store_page import StorePage
        self._store_page_index = self._stack.count()
        store_page = StorePage(self)
        self._add_page(tr("settings.nav.store"), store_page)
        # Apply the badge now that the nav item exists (covers the case where
        # the index resolved synchronously during the page's construction).
        self.set_store_badge(store_page.update_count())

        # … add-on and external modules at the very bottom, set apart by a
        # divider, so core program and add-ons are clearly distinguishable.
        if externals:
            self._add_nav_separator()
            for module in externals:
                widget = module.get_settings_widget()
                self._module_pages[module.MODULE_ID] = self._stack.count()
                self._add_page(module.DISPLAY_NAME, widget)

        self._add_page(tr("settings.nav.about"), self._build_about_page())

        self._fit_nav_height()

    def _logo_pixmap(self, size: int):
        """A crisp logo pixmap: rendered from the SVG at the display's pixel
        ratio, falling back to the best-matching .ico frame."""
        from PySide6.QtGui import QIcon, QPainter, QPixmap
        from withease.core.resources import app_icon_path, app_svg_path

        dpr = self.devicePixelRatioF() or 1.0
        svg = app_svg_path()
        if svg.exists():
            from PySide6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(str(svg))
            if renderer.isValid():
                px = max(1, int(round(size * dpr)))
                pm = QPixmap(px, px)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                renderer.render(p)
                p.end()
                pm.setDevicePixelRatio(dpr)
                return pm
        ico = app_icon_path()
        if ico.exists():
            return QIcon(str(ico)).pixmap(size, size)
        return None

    def _build_about_page(self) -> QWidget:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from withease import __version__
        from withease.gui.ui_utils import WrappingLabel, card, em

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Keep the text column at a comfortable reading width instead of
        # stretching across the whole (wide) window.
        _MAXW = 640
        _BTNW = 200

        def _btn(key: str) -> QPushButton:
            b = QPushButton(tr(key))
            # Minimum (not fixed) width so larger font sizes never clip the
            # label; the button grows with its text instead.
            b.setMinimumWidth(_BTNW)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        # --- Title + description as full-width rows --------------------------
        # No logo here on purpose: the WithEase logo is already shown prominently
        # in the sidebar, and a logo beside/above word-wrapping text repeatedly
        # overlapped it (Qt under-reserves the row height for a fixed-size image
        # next to a wrapping label).  Plain full-width rows can never overlap –
        # at any window width or font size.
        layout.addWidget(self._page_title("WithEase"))

        # Same heading-then-separator convention as every module page
        # (enable-checkbox followed immediately by an HLine) – this page's
        # title had none, which read as inconsistent with the rest of the app.
        title_sep = QFrame()
        title_sep.setFrameShape(QFrame.Shape.HLine)
        title_sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(title_sep)

        desc = WrappingLabel(tr("about.description"))
        desc.setWordWrap(True)
        desc.setMaximumWidth(_MAXW)
        layout.addWidget(desc)

        meta = QLabel(
            f"{tr('about.version')} {__version__}   ·   {tr('about.license')}")
        meta.setStyleSheet(theme.hint_style())
        layout.addWidget(meta)

        vibe = WrappingLabel(tr("about.vibe"))
        vibe.setWordWrap(True)
        vibe.setMaximumWidth(_MAXW)
        vibe.setStyleSheet(theme.hint_style())
        layout.addWidget(vibe)

        # --- Share the project ----------------------------------------------
        # The "/releases/latest" link always resolves to the newest release, so
        # anyone the user shares it with lands on the current download.
        share_card, share_body = card(tr("about.share.heading"), "🔗")

        share_hint = WrappingLabel(tr("about.share.hint"))
        share_hint.setWordWrap(True)
        share_hint.setMaximumWidth(_MAXW)
        share_body.addWidget(share_hint)

        # Real, clickable hyperlink (opens in the browser); still selectable.
        link = QLabel(
            f'<a href="{self.RELEASE_URL}" '
            f'style="color:{theme.accent()};text-decoration:none;">'
            f'{self.RELEASE_URL}</a>')
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        share_body.addWidget(link)

        share_body.addSpacing(em(0.7))  # a bit of air before the buttons
        share_row = QHBoxLayout()
        self._copy_link_btn = _btn("about.share.copy")
        self._copy_link_btn.clicked.connect(self._copy_release_link)
        share_row.addWidget(self._copy_link_btn)

        open_release = _btn("about.share.open")
        open_release.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.RELEASE_URL)))
        share_row.addWidget(open_release)
        share_row.addStretch()
        share_body.addLayout(share_row)
        layout.addWidget(share_card)

        # --- Support / Ko-fi -------------------------------------------------
        # A friendly, entirely optional donation nudge – the program stays free.
        support_card, support_body = card(tr("about.support.heading"), "☕")

        support_text = WrappingLabel(tr("about.support.text"))
        support_text.setWordWrap(True)
        support_text.setMaximumWidth(_MAXW)
        support_body.addWidget(support_text)
        support_body.addSpacing(em(0.7))  # a bit of air before the button

        kofi_url = "https://ko-fi.com/xelagibiel"
        kofi = _btn("about.support.button")
        kofi.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(kofi_url)))
        support_row = QHBoxLayout()
        support_row.addWidget(kofi)
        support_row.addStretch()
        support_body.addLayout(support_row)
        layout.addWidget(support_card)

        # --- More links ------------------------------------------------------
        feedback_card, feedback_body = card(tr("about.feedback.heading"), "💬")

        feedback_hint = WrappingLabel(tr("about.feedback.hint"))
        feedback_hint.setWordWrap(True)
        feedback_hint.setMaximumWidth(_MAXW)
        feedback_body.addWidget(feedback_hint)
        feedback_body.addSpacing(em(0.7))  # a bit of air before the buttons

        links_row = QHBoxLayout()
        gh_url = "https://github.com/XelaGibiel/WithEase"
        gh = _btn("about.github")
        gh.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(gh_url)))
        links_row.addWidget(gh)

        feedback = _btn("about.feedback")
        feedback.clicked.connect(self._open_feedback)
        links_row.addWidget(feedback)
        links_row.addStretch()
        feedback_body.addLayout(links_row)
        layout.addWidget(feedback_card)

        layout.addStretch()
        return self._scrollable(widget)

    def _copy_release_link(self) -> None:
        """Copy the latest-release link to the clipboard and confirm briefly."""
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.RELEASE_URL)
        btn = self._copy_link_btn
        btn.setText(tr("about.share.copied"))
        btn.setEnabled(False)
        QTimer.singleShot(
            2000, lambda: (btn.setText(tr("about.share.copy")),
                           btn.setEnabled(True)))

    def _open_feedback(self) -> None:
        from withease.gui.feedback_dialog import FeedbackDialog
        FeedbackDialog(self).exec()

    def set_store_badge(self, count: int) -> None:
        """Show the number of available module updates on the 'Module' nav
        entry (e.g. 'Module (2)'), or the plain label when there are none."""
        base = tr("settings.nav.store")
        text = f"{base}  ({count})" if count > 0 else base
        for i in range(self._nav.count()):
            item = self._nav.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == getattr(
                    self, "_store_page_index", -1):
                item.setText(text)
                return

    def _add_page(self, label: str, widget: QWidget) -> None:
        from withease.gui.ui_utils import em
        item = QListWidgetItem(label)
        item.setSizeHint(item.sizeHint().__class__(140, max(36, em(2))))
        item.setData(Qt.ItemDataRole.UserRole, self._stack.count())
        self._nav.addItem(item)
        self._stack.addWidget(widget)

    def _add_nav_separator(self) -> None:
        # Invisible spacer between core modules and add-ons: pure spacing,
        # no drawn line (in any theme) – the gap alone groups the entries.
        from withease.gui.ui_utils import em
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)  # not selectable, no page
        item.setSizeHint(item.sizeHint().__class__(140, max(11, em(0.6))))
        self._nav.addItem(item)

    def _fit_nav_height(self) -> None:
        """The list expands to fill the sidebar (see _build_ui) and scrolls when
        too short, so it only needs to be allowed to shrink for the scrollbar to
        kick in – no fixed height."""
        self._nav.setMinimumHeight(0)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @staticmethod
    def _scrollable(inner: QWidget) -> QWidget:
        """Wrap a page so its content is never clipped when the window is short:
        a vertical scrollbar appears only when the content is taller than the
        viewport (WCAG: no truncated text, works at any size / font size)."""
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        return scroll

    @staticmethod
    def _page_title(text: str) -> QLabel:
        """Page heading – same size/weight as the module enable-checkboxes,
        so all sidebar pages start with a uniform title."""
        label = QLabel(text)
        label.setStyleSheet(theme.title_style())
        return label

    def _build_general_page(self) -> QWidget:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import (
            QButtonGroup, QCheckBox, QScrollArea, QSpinBox,
        )
        from withease.core import autostart, resources
        from withease.gui.ui_utils import card, em, label_with_hint
        from withease.gui.widgets.hotkey_edit import HotkeyEdit

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(30, 28, 30, 28)
        outer.setSpacing(18)

        outer.addWidget(self._page_title(tr("settings.general.title")))
        general_sep = QFrame()
        general_sep.setFrameShape(QFrame.Shape.HLine)
        general_sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(general_sep)
        outer.addSpacing(2)

        def _form() -> QFormLayout:
            f = QFormLayout()
            f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            f.setHorizontalSpacing(16)
            f.setVerticalSpacing(12)
            f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                | Qt.AlignmentFlag.AlignVCenter)
            return f

        # ---- Card 1: Appearance -----------------------------------------
        appearance, appearance_body = card(
            tr("settings.general.card.appearance"), "🎨")
        form = _form()

        # Flag PNGs (not emoji – flag emoji don't reliably render as an actual
        # flag glyph in every Qt/Windows font-fallback combination).
        _LANG_COUNTRY = {"de": "de", "en": "gb"}
        self._lang_combo = QComboBox()
        # No minimum width: compact_fields() sets AdjustToContents, so the box
        # ends up exactly as wide as its longest entry instead of reserving a
        # fixed slab of space.
        self._lang_combo.setIconSize(QSize(em(1.1), round(em(1.1) * 2 / 3)))
        current_lang = self._app._app_config.get("language", "de")
        for code, display_name in SUPPORTED_LANGUAGES.items():
            icon = QIcon()
            country = _LANG_COUNTRY.get(code)
            if country:
                path = resources.flag_icon_path(country)
                if path.exists():
                    icon = QIcon(str(path))
            self._lang_combo.addItem(icon, display_name, userData=code)
            if code == current_lang:
                self._lang_combo.setCurrentIndex(self._lang_combo.count() - 1)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow(tr("settings.general.language"), self._lang_combo)

        # Theme as a small exclusive button group (not a dropdown) – all three
        # options are visible and recognisable by icon at a glance, matching
        # the "as simple/obvious as possible" goal from the UX review.
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        current_theme = self._app._app_config.get("theme", "system")
        for key, icon in (("system", "🖥"), ("light", "☀"), ("dark", "🌙")):
            btn = QPushButton(f"{icon}  {tr(f'settings.general.theme.{key}')}")
            btn.setCheckable(True)
            btn.setChecked(key == current_theme)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, k=key: self._apply_appearance("theme", k))
            self._theme_group.addButton(btn)
            theme_row.addWidget(btn)
        theme_row.addStretch()

        # High contrast is independent of the light/dark/system choice (any
        # of the three can also run with boosted contrast), so it is a
        # separate toggle button – visually grouped with Design, but not
        # part of the exclusive group above.  On its own row underneath
        # (not appended to theme_row) so it never gets pushed past the card
        # edge at larger font sizes, where the three theme buttons alone
        # already fill most of the row width.
        contrast_row = QHBoxLayout()
        contrast_row.setSpacing(6)
        self._contrast_btn = QPushButton(
            f"◐  {tr('settings.general.contrast')}")
        self._contrast_btn.setCheckable(True)
        self._contrast_btn.setChecked(
            self._app._app_config.get("contrast", "normal") == "high")
        self._contrast_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._contrast_btn.setToolTip(tr("settings.general.contrast.hint"))
        self._contrast_btn.toggled.connect(self._on_contrast_toggled)
        contrast_row.addWidget(self._contrast_btn)
        contrast_row.addStretch()

        theme_col = QVBoxLayout()
        theme_col.setSpacing(6)
        theme_col.addLayout(theme_row)
        theme_col.addLayout(contrast_row)
        form.addRow(tr("settings.general.theme"), theme_col)

        # 8–16 pt, or "system default" (stored as 0).
        self._font_combo = QComboBox()
        self._font_combo.addItem(tr("settings.general.font_size.system"), 0)
        for pt in range(8, 17):
            self._font_combo.addItem(f"{pt} pt", pt)
        saved_pt = int(self._app._app_config.get("font_size", 0))
        idx = self._font_combo.findData(saved_pt if 8 <= saved_pt <= 16 else 0)
        self._font_combo.setCurrentIndex(idx if idx >= 0 else 0)
        # Deliberately NOT applied live: changing the font rebuilds every page,
        # which recreates this very combo box – so the change only takes
        # effect once the "Apply" button next to it is pressed.
        self._font_combo.currentIndexChanged.connect(self._on_font_size_pending)
        self._font_apply_btn = QPushButton()
        # Slim self-drawn arrow in the action blue – the stock QStyle reload
        # icon was a heavy filled glyph that looked bold next to the text.
        self._font_apply_btn.setIcon(theme.refresh_icon(theme.action_color()))
        self._font_apply_btn.setFixedSize(em(2), em(2))
        # The icon itself has its own pixel size independent of the button
        # box – without this it stays at Qt's small default and barely grows
        # when the box does, at larger font sizes it looked stuck tiny.
        self._font_apply_btn.setIconSize(QSize(em(1.2), em(1.2)))
        self._font_apply_btn.setToolTip(tr("settings.general.font_size.apply"))
        self._font_apply_btn.setAccessibleName(
            tr("settings.general.font_size.apply"))
        self._font_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._font_apply_btn.setEnabled(False)     # nothing pending yet
        self._font_apply_btn.clicked.connect(self._on_font_size_apply)
        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        font_row.addWidget(self._font_combo)
        font_row.addWidget(self._font_apply_btn)
        font_row.addStretch()
        form.addRow(tr("settings.general.font_size"), font_row)

        self._hints_cb = QCheckBox(tr("settings.general.hints_enabled"))
        self._hints_cb.setChecked(
            bool(self._app._app_config.get("hints_enabled", True)))
        self._hints_cb.toggled.connect(self._on_hints_toggled)
        form.addRow("", self._hints_cb)

        # Central chip size for the Sticky-Keys and macro-mode overlay chips
        # (they used to be two separate per-module settings – consolidated
        # here since both control the same kind of thing app-wide).
        self._chip_size_spin = QSpinBox()
        self._chip_size_spin.setRange(16, 64)
        self._chip_size_spin.setSuffix(" px")
        self._chip_size_spin.setValue(
            int(self._app._app_config.get("overlay_chip_size", 28)))
        self._chip_size_spin.valueChanged.connect(
            self._on_overlay_chip_size_changed)
        self._chip_preview_cb = QCheckBox(
            tr("settings.general.overlay_chip_size.preview"))
        self._chip_preview_cb.toggled.connect(
            self._on_overlay_chip_preview_toggled)
        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        chip_row.addWidget(self._chip_size_spin)
        chip_row.addWidget(self._chip_preview_cb)
        chip_row.addStretch()
        form.addRow(
            label_with_hint(tr("settings.general.overlay_chip_size"),
                            tr("settings.general.overlay_chip_size.hint")),
            chip_row)

        appearance_body.addLayout(form)
        outer.addWidget(appearance)

        # ---- Card 2: System ---------------------------------------------
        system, system_body = card(tr("settings.general.card.system"), "🖥")
        sys_form = _form()
        from withease.gui.widgets.hint_icon import HintIcon
        self._autostart_cb = QCheckBox(tr("settings.general.autostart"))
        self._autostart_cb.setChecked(autostart.is_enabled())
        self._autostart_cb.toggled.connect(self._on_autostart_toggled)
        autostart_row = QHBoxLayout()
        autostart_row.setContentsMargins(0, 0, 0, 0)
        autostart_row.setSpacing(6)
        autostart_row.addWidget(self._autostart_cb)
        autostart_row.addWidget(
            HintIcon(tr("settings.general.autostart.hint")))
        autostart_row.addStretch(1)
        sys_form.addRow("", autostart_row)
        system_body.addLayout(sys_form)
        outer.addWidget(system)

        # ---- Card 3: Emergency stop -------------------------------------
        emg_card, emg_body = card(
            tr("settings.general.card.emergency"), "🛑", danger=True)
        emg_form = _form()
        emergency = self._app._profile_data.get("emergency_key", "F12")
        if (emergency and "+" not in emergency
                and not (emergency.startswith("Key.")
                         or emergency.startswith("'"))):
            emergency = f"Key.{emergency.lower()}"
        self._emergency_edit = HotkeyEdit(emergency,
                                          action_id="app.emergency_stop")
        self._emergency_edit.key_changed.connect(self._on_emergency_key_changed)
        emg_form.addRow(tr("settings.general.emergency_key"),
                        self._emergency_edit)
        emg_body.addLayout(emg_form)

        # Warning shown when no emergency key is set (only tray/button remain).
        self._emergency_warning = QLabel(
            tr("settings.general.emergency_key.empty_warning"))
        self._emergency_warning.setStyleSheet(theme.warn_style())
        self._emergency_warning.setWordWrap(True)
        self._emergency_warning.setVisible(not emergency)
        emg_body.addWidget(self._emergency_warning)

        emergency_desc = QLabel(tr("settings.general.emergency_key.description"))
        emergency_desc.setStyleSheet(theme.hint_style())
        emergency_desc.setWordWrap(True)
        emg_body.addWidget(emergency_desc)
        outer.addWidget(emg_card)

        outer.addStretch()

        # Wrap in a scroll area so the cards never clip at large font sizes.
        # Small subclass so leaving this page always clears the chip preview
        # (matches the reset the old per-module pages did in their own
        # hideEvent) – a stray preview chip left visible after navigating
        # away would otherwise look like a bug.
        win = self

        class _GeneralScrollArea(QScrollArea):
            def hideEvent(self, event: object) -> None:  # noqa: N802
                cb = getattr(win, "_chip_preview_cb", None)
                if cb is not None and cb.isChecked():
                    cb.setChecked(False)
                super().hideEvent(event)  # type: ignore[arg-type]

        scroll = _GeneralScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _apply_appearance(self, key: str, value) -> None:
        """Persist one appearance setting and re-apply the whole theme
        (scheme + contrast + font size always travel together, otherwise
        changing one would silently reset the others)."""
        cfg = self._app._app_config
        cfg[key] = value
        from withease.core import config
        config.save_app_config(cfg)
        from withease.gui.theme import apply_theme
        apply_theme(self._app._qt_app, cfg.get("theme", "system"),
                    cfg.get("contrast", "normal"),
                    int(cfg.get("font_size", 0)))

    def _on_contrast_toggled(self, on: bool) -> None:
        self._apply_appearance("contrast", "high" if on else "normal")

    def _pending_font_size(self) -> int:
        """The combo box's stored font size (0 = system)."""
        return int(self._font_combo.currentData() or 0)

    def _on_font_size_pending(self, _index: int) -> None:
        # Don't apply yet – just enable "Apply" while the value differs from
        # what's active, so the combo box stays put until the user confirms.
        changed = (self._pending_font_size()
                   != int(self._app._app_config.get("font_size", 0)))
        self._font_apply_btn.setEnabled(changed)

    def _on_font_size_apply(self) -> None:
        value = self._pending_font_size()
        if value == int(self._app._app_config.get("font_size", 0)):
            self._font_apply_btn.setEnabled(False)
            return  # unchanged – no rebuild needed
        # Give the recreated combo box focus again so it stays reachable via
        # keyboard after the pages were rebuilt.
        self._refocus_font_combo = self._font_combo.hasFocus()
        self._apply_appearance("font_size", value)

    def _on_hints_toggled(self, enabled: bool) -> None:
        # Deliberately NOT routed through _apply_appearance – that triggers a
        # full apply_theme()+page rebuild, overkill for a pure show/hide.
        from withease.core import config
        from withease.gui.widgets.hint_icon import set_hints_visible
        self._app._app_config["hints_enabled"] = enabled
        config.save_app_config(self._app._app_config)
        set_hints_visible(enabled)

    def _on_overlay_chip_size_changed(self, size: int) -> None:
        from withease.core import config
        self._app._app_config["overlay_chip_size"] = size
        config.save_app_config(self._app._app_config)
        # The sticky-keys and macro-mode indicators already subscribe to
        # these two topics (unrelated to this page) – publishing to both
        # keeps them fully unchanged while the setting's source moves here.
        bus.publish("macros.chip_size", size=size)
        bus.publish("keyboard.chip_size", size=size)

    def _on_overlay_chip_preview_toggled(self, active: bool) -> None:
        bus.publish("macros.preview", active=active)
        bus.publish("keyboard.preview", active=active)

    def _on_emergency_key_changed(self, key: str) -> None:
        self._app.set_emergency_key(key)
        if hasattr(self, "_emergency_warning"):
            self._emergency_warning.setVisible(not key)

    def _on_autostart_toggled(self, enabled: bool) -> None:
        from withease.core import autostart
        if not autostart.set_enabled(enabled):
            # Write blocked (e.g. by security software) – revert and explain.
            self._autostart_cb.blockSignals(True)
            self._autostart_cb.setChecked(autostart.is_enabled())
            self._autostart_cb.blockSignals(False)
            if enabled:
                QMessageBox.warning(
                    self, tr("settings.general.autostart.blocked.title"),
                    tr("settings.general.autostart.blocked"))
            return
        self._app._app_config["autostart"] = enabled
        from withease.core import config
        config.save_app_config(self._app._app_config)

    def _on_language_changed(self, index: int) -> None:
        lang_code = self._lang_combo.itemData(index)
        i18n_module.load(lang_code)
        self._app._app_config["language"] = lang_code
        from withease.core import config
        config.save_app_config(self._app._app_config)
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Rebuild the entire window content (language or profile change)."""
        self._rebuilding = True
        # Paint once at the end instead of after every page that is torn down
        # and rebuilt – without this the window visibly flickers through a
        # half-built state on a theme switch.
        self.setUpdatesEnabled(False)
        try:
            self._do_rebuild_ui()
        finally:
            self.setUpdatesEnabled(True)
            self._rebuilding = False

    def _do_rebuild_ui(self) -> None:
        current_row = self._nav.currentRow()

        # Remove all nav items and stack pages
        self._nav.clear()
        while self._stack.count():
            widget = self._stack.widget(0)
            self._stack.removeWidget(widget)
            widget.deleteLater()

        # Rebuild
        self.setWindowTitle(tr("settings.title"))
        # The nav/sidebar widgets survive the rebuild – refresh everything
        # that depends on theme or font size (nav QSS is central in
        # app_stylesheet; just re-assert the font and the width).
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app is not None:
            self._nav.setFont(_app.font())
        from withease.gui.ui_utils import em
        self._sidebar.setFixedWidth(max(170, em(10)))
        self._populate_nav()
        from withease.gui.ui_utils import compact_fields
        compact_fields(self._stack)
        self._refresh_search_index()

        # Update sidebar/footer widgets that are outside the stack
        self._update_emergency_btn()
        self._update_footer_profile()
        if self._latest_release is None:
            self._version_btn.setText(
                tr("app.update.version", version=__version__))
        else:
            self._on_update_check_done(self._latest_release)

        # Restore selected page (or go back to General), skipping separators
        self._select_nav_row(max(0, current_row))

        if getattr(self, "_refocus_font_combo", False):
            self._refocus_font_combo = False
            self._font_combo.setFocus()

    def _build_profiles_page(self) -> QWidget:
        from PySide6.QtWidgets import QListWidget
        from withease.gui.ui_utils import card

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self._page_title(tr("settings.profiles.title")))
        profiles_sep = QFrame()
        profiles_sep.setFrameShape(QFrame.Shape.HLine)
        profiles_sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(profiles_sep)

        desc = QLabel(tr("settings.profiles.description") + " "
                      + tr("settings.profiles.tray_hint"))
        desc.setStyleSheet(theme.hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Same card() frame every other page uses – a bare list+buttons block
        # looked out of place next to Allgemein/Maus/etc.
        profiles_card, profiles_body = card(
            tr("settings.profiles.card"), "👤")

        self._profiles_list = QListWidget()
        self._profiles_list.setMinimumHeight(160)
        theme.style_item_view(self._profiles_list, "QListWidget")
        self._profiles_list.doubleClicked.connect(
            lambda _: self._on_profile_activate())
        self._profiles_list.currentRowChanged.connect(
            lambda _: self._update_profile_buttons())
        profiles_body.addWidget(self._profiles_list)

        btn_row = QHBoxLayout()
        self._profile_activate_btn = QPushButton(tr("settings.profiles.activate"))
        self._profile_activate_btn.clicked.connect(self._on_profile_activate)
        btn_row.addWidget(self._profile_activate_btn)
        self._profile_new_btn = QPushButton(tr("settings.profiles.new"))
        self._profile_new_btn.clicked.connect(self._on_profile_new)
        btn_row.addWidget(self._profile_new_btn)
        self._profile_rename_btn = QPushButton(tr("settings.profiles.rename"))
        self._profile_rename_btn.clicked.connect(self._on_profile_rename)
        btn_row.addWidget(self._profile_rename_btn)
        self._profile_delete_btn = QPushButton(tr("settings.profiles.delete"))
        mark_danger(self._profile_delete_btn)
        self._profile_delete_btn.clicked.connect(self._on_profile_delete)
        btn_row.addWidget(self._profile_delete_btn)
        btn_row.addStretch()
        profiles_body.addLayout(btn_row)

        layout.addWidget(profiles_card)
        layout.addStretch()
        self._refresh_profiles_list()
        return self._scrollable(widget)

    # -- Profiles page handlers -----------------------------------------

    def _refresh_profiles_list(self) -> None:
        if not hasattr(self, "_profiles_list"):
            return
        active = self._app.active_profile
        self._profiles_list.clear()
        for name in sorted(self._app.list_profiles(), key=str.lower):
            label = (f"●  {name}   ({tr('settings.profiles.active_marker')})"
                     if name == active else f"    {name}")
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._profiles_list.addItem(item)
        self._update_profile_buttons()

    def _selected_profile(self) -> str | None:
        item = self._profiles_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _update_profile_buttons(self) -> None:
        sel = self._selected_profile()
        is_active = sel == self._app.active_profile
        self._profile_activate_btn.setEnabled(bool(sel) and not is_active)
        self._profile_rename_btn.setEnabled(bool(sel))
        self._profile_delete_btn.setEnabled(bool(sel) and not is_active)

    def _on_profiles_changed(self, switched: bool = False, **_: object) -> None:
        if switched:
            # Module settings objects were replaced – rebuild all pages
            # (deferred: the event may arrive mid-click on the old page).
            # The activation toast comes AFTER the rebuild, because widget
            # construction triggers saves whose toast would overwrite it.
            def rebuild_and_notify() -> None:
                self._rebuild_ui()
                if self.isVisible():
                    self._save_toast.show_message(
                        tr("settings.profile_activated",
                           name=self._app.active_profile))
            QTimer.singleShot(0, rebuild_and_notify)
        else:
            self._refresh_profiles_list()
        self._update_footer_profile()

    def _on_profile_activate(self) -> None:
        sel = self._selected_profile()
        if not sel or sel == self._app.active_profile:
            return
        self._app.switch_profile(sel)  # rebuild follows via profiles.changed

    def _on_profile_new(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, tr("settings.profiles.new"),
            tr("settings.profiles.name.prompt"))
        if ok and name.strip():
            self._app.create_profile(name)  # switches → rebuild via event

    def _on_profile_rename(self) -> None:
        sel = self._selected_profile()
        if not sel:
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, tr("settings.profiles.rename"),
            tr("settings.profiles.name.prompt"), text=sel)
        if ok and name.strip() and name.strip() != sel:
            self._app.rename_profile(sel, name)
            self._refresh_profiles_list()

    def _on_profile_delete(self) -> None:
        sel = self._selected_profile()
        if not sel or sel == self._app.active_profile:
            return
        # Snapshot before deleting: the file is what makes undo possible, and
        # taking it back must not need a second precise click first.
        from withease.core import config as _config
        from withease.gui.widgets.undo_bar import show_undo
        try:
            snapshot = _config.load_profile(sel)
        except Exception:
            snapshot = None
        if not self._app.delete_profile(sel):
            return
        self._refresh_profiles_list()

        def undo(name: str = sel, data=snapshot) -> None:
            if data is None:
                return
            _config.save_profile(name, data)
            bus.publish("profiles.changed", switched=False)
            self._refresh_profiles_list()

        show_undo(self, tr("undo.profile", name=sel), undo)

    def _build_actions_page(self) -> QWidget:
        from PySide6.QtWidgets import QCheckBox, QTableWidget, QTableWidgetItem
        from withease.gui.ui_utils import card
        from withease.gui.widgets.collapsible_section import CollapsibleSection
        from withease.gui.widgets.hotkey_edit import HotkeyEdit
        from withease.gui.widgets.resize_strip import ResizeStrip

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self._page_title(tr("settings.actions.title")))
        actions_sep = QFrame()
        actions_sep.setFrameShape(QFrame.Shape.HLine)
        actions_sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(actions_sep)

        desc = QLabel(tr("settings.actions.overview_hint"))
        desc.setStyleSheet(theme.hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        _TARGET_PX = theme.target_px()
        actions_card, actions_body = card(tr("settings.actions.card"), "⚡")

        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels([
            tr("settings.actions.col.favorite"),
            tr("settings.actions.action_col"),
            tr("settings.actions.trigger_col"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        # At least one full click target wide, so the star column can
        # centre its checkbox instead of the box overflowing to the right.
        table.setColumnWidth(0, max(60, _TARGET_PX))
        table.setColumnWidth(1, 260)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        # Rows tall enough for a full-size favourite checkbox (see _TARGET_PX).
        table.verticalHeader().setDefaultSectionSize(_TARGET_PX)
        # Height is pinned (and made draggable) further down via ResizeStrip.
        from PySide6.QtWidgets import QStyleFactory
        style = QStyleFactory.create("Fusion")
        if style is not None:
            table.setStyle(style)
        theme.style_item_view(table, "QTableWidget")

        # Rows: favourites first (in overlay order), then everything else.
        # Only ACTIVE entries are listed: actions whose tool is enabled have a
        # trigger assigned (disabled tools are gated to an empty trigger).
        from PySide6.QtWidgets import QLineEdit
        from withease.core.action_manager import action_manager

        search = QLineEdit()
        search.setPlaceholderText(tr("settings.actions.search"))
        search.setClearButtonEnabled(True)
        actions_body.addWidget(search)

        def collect_entries() -> list[tuple[str, str, str]]:
            favorites = self._app.get_favorites()
            all_entries: list[tuple[str, str, str]] = []  # (fid, label, key)
            for a in sorted(action_manager.get_all(),
                            key=lambda a: a.label.lower()):
                # Inactive actions are hidden – EXCEPT favourites, which must
                # stay listed so their star can always be removed again.
                if not a.trigger and a.id not in favorites:
                    continue
                key = HotkeyEdit._format_key(a.trigger) if a.trigger else "—"
                all_entries.append((a.id, a.label, key))
            for module in self._app.get_modules():
                if module.MODULE_ID == "macros":
                    # The macro-mode trigger key itself.
                    trigger = module._settings.get("trigger_key", "")
                    fid = "macros.trigger"
                    if (module.enabled and trigger) or fid in favorites:
                        key = (HotkeyEdit._format_key(trigger)
                               if module.enabled and trigger else "—")
                        all_entries.insert(0, (
                            fid, tr("module.macros.trigger_key"), key))
                    for m in getattr(module, "_macros", []):
                        fid = f"macro:{m.id}"
                        if not module.enabled and fid not in favorites:
                            continue
                        key = (HotkeyEdit._format_key(m.trigger_key)
                               if module.enabled and m.trigger_key else "—")
                        label = (f"{tr('settings.actions.macro_prefix')} "
                                 f"{m.label}")
                        all_entries.append((fid, label, key))

            needle = search.text().strip().lower()
            if needle:
                all_entries = [
                    e for e in all_entries
                    if needle in e[1].lower() or needle in e[2].lower()
                ]

            favorites = self._app.get_favorites()
            by_fid = {fid: (fid, label, key) for fid, label, key in all_entries}
            ordered = [by_fid[f] for f in favorites if f in by_fid]
            ordered += [e for e in all_entries if e[0] not in favorites]
            return ordered

        def selected_fid() -> str | None:
            row = table.currentRow()
            item = table.item(row, 1) if row >= 0 else None
            return item.data(Qt.ItemDataRole.UserRole) if item else None

        def refresh_table(keep_fid: str | None = None) -> None:
            entries = collect_entries()
            table.setRowCount(len(entries))
            for row, (fid, label, key) in enumerate(entries):
                cb = QCheckBox()
                cb.setChecked(self._app.is_favorite(fid))

                def on_toggled(checked: bool, f: str = fid) -> None:
                    if checked != self._app.is_favorite(f):
                        self._app.toggle_favorite(f)
                        # Re-sort AFTER the signal finished (the checkbox is
                        # deleted during the rebuild – never mid-signal).
                        def resort(fid_: str = f) -> None:
                            import shiboken6
                            if shiboken6.isValid(table):
                                refresh_table(fid_)
                        QTimer.singleShot(0, resort)

                cb.toggled.connect(on_toggled)
                # A text-less checkbox draws its indicator at its LEFT edge, so
                # stretching the checkbox itself to the accessible target size
                # pushed the box off-centre (and out of the 60px column).
                # Instead the CELL is the click target: it is a full row high
                # and a full target wide, forwards its clicks to the checkbox,
                # and the indicator sits exactly in the middle of the column.
                cell = _FavouriteCell(cb)
                table.setCellWidget(row, 0, cell)
                row_tip = tr("settings.actions.row_tooltip")
                label_item = QTableWidgetItem(label)
                label_item.setData(Qt.ItemDataRole.UserRole, fid)
                label_item.setToolTip(row_tip)
                table.setItem(row, 1, label_item)
                key_item = QTableWidgetItem(key)
                key_item.setToolTip(row_tip)
                table.setItem(row, 2, key_item)
                if keep_fid is not None and fid == keep_fid:
                    table.setCurrentCell(row, 1)

        refresh_table()
        search.textChanged.connect(lambda _t: refresh_table(selected_fid()))

        # Live refresh: tools being enabled/disabled changes which actions
        # are active.  Deferred so we never rebuild mid-signal.  The page may
        # be torn down (theme/language rebuild) before the timer fires, so
        # every deferred refresh checks that the table still exists.
        def deferred_refresh() -> None:
            import shiboken6
            if shiboken6.isValid(table):
                refresh_table(selected_fid())

        def on_module_event(**_: object) -> None:
            QTimer.singleShot(0, deferred_refresh)

        for event in ("module.settings_changed", "module.started",
                      "module.stopped"):
            bus.subscribe(event, on_module_event)
        widget.destroyed.connect(lambda: [
            bus.unsubscribe(e, on_module_event)
            for e in ("module.settings_changed", "module.started",
                      "module.stopped")
        ])

        # Right-click on a row → jump straight to the settings page of the
        # module the action/macro belongs to.
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def page_for_fid(fid: str) -> int | None:
            module_id = ("macros" if fid.startswith("macro:")
                         else fid.split(".")[0])
            if module_id == "app":       # e.g. app.emergency_stop
                module_id = "general"
            return self._module_pages.get(module_id)

        def on_context_menu(pos) -> None:
            row = table.indexAt(pos).row()
            if row < 0:
                return
            fid = table.item(row, 1).data(Qt.ItemDataRole.UserRole)
            page = page_for_fid(fid) if fid else None
            if page is None:
                return
            from PySide6.QtWidgets import QMenu
            menu = QMenu(table)
            menu.addAction(tr("settings.actions.goto"),
                           lambda: self._goto_page(page))
            menu.exec(table.viewport().mapToGlobal(pos))

        table.customContextMenuRequested.connect(on_context_menu)

        # A plain, always-visible button doing the same thing as the context
        # menu.  The jump used to be reachable ONLY by right-click, which is
        # the hardest mouse action for the people this app is for – and is not
        # reachable at all by keyboard or a switch.
        goto_btn = QPushButton(tr("settings.actions.goto.button"))
        goto_btn.setToolTip(tr("settings.actions.goto"))
        goto_btn.setEnabled(False)

        def target_page_for_selection() -> int | None:
            fid = selected_fid()
            return page_for_fid(fid) if fid else None

        def sync_goto_btn() -> None:
            goto_btn.setEnabled(target_page_for_selection() is not None)

        def on_goto_clicked() -> None:
            page = target_page_for_selection()
            if page is not None:
                self._goto_page(page)

        goto_btn.clicked.connect(on_goto_clicked)
        table.itemSelectionChanged.connect(sync_goto_btn)
        # Double-click a row does the same (mirrors the macro table), so both
        # the pointer-light and the pointer-heavy path lead to the same place.
        table.doubleClicked.connect(lambda _idx: on_goto_clicked())

        actions_body.addWidget(table)
        # Same drag handle the macro table has, so a long action list can be
        # pulled taller instead of scrolling inside a fixed-height box.
        # setMinimumHeight above is a floor, not a fixed size – the strip needs
        # a fixed height to drag against, so pin it to the current one first.
        table.setFixedHeight(220)
        actions_body.addWidget(ResizeStrip(table))

        # ▲▼ reorder the favourite block at the top of the table.
        from withease.gui.widgets.hint_icon import HintIcon
        move_row = QHBoxLayout()
        move_row.addWidget(QLabel(tr("settings.actions.order_label")))
        move_row.addWidget(HintIcon(tr("settings.actions.order_hint")))
        move_row.addStretch()
        for arrow, delta, tip_key in (
                ("▲", -1, "settings.actions.order.up"),
                ("▼", 1, "settings.actions.order.down")):
            # Plain buttons at their natural size – same look as the Makros
            # reorder arrows (no fixed width / iconBtn styling).
            btn = QPushButton(arrow)
            btn.setToolTip(tr(tip_key))
            btn.setAccessibleName(tr(tip_key))

            def on_move(_checked: bool = False, d: int = delta) -> None:
                fid = selected_fid()
                if fid and self._app.is_favorite(fid):
                    self._app.move_favorite(fid, d)
                    refresh_table(fid)

            btn.clicked.connect(on_move)
            move_row.addWidget(btn)
        move_row.addWidget(goto_btn)
        actions_body.addLayout(move_row)
        layout.addWidget(actions_card)

        # ── Overlay settings ─────────────────────────────────────────
        # Same CollapsibleSection every module page uses for an optional
        # feature (see mouse_settings.py) – was a bespoke bold-checkbox +
        # bare-widget block before, now framed like everything else.
        cfg = self._app.get_overlay_config()

        overlay_section = CollapsibleSection(
            tr("settings.actions.overlay"),
            checked=bool(cfg.get("enabled", False)),
            description=tr("settings.actions.overlay.description"),
            icon="📌")
        overlay_section.toggled.connect(
            lambda v: self._app.set_overlay_option("enabled", v))

        form = QFormLayout()
        form.setSpacing(8)

        from withease.gui.widgets.actions_overlay import POSITIONS
        from withease.gui.ui_utils import (label_with_hint,
                                          set_option_hint)
        pos_combo = QComboBox()
        for pos in POSITIONS:
            label = (tr("settings.actions.overlay.pos.custom")
                     if pos == "custom" else tr(f"keyboard.indicator.pos.{pos}"))
            pos_combo.addItem(label, pos)
            if pos == "custom":
                set_option_hint(pos_combo, pos_combo.count() - 1,
                                tr("settings.actions.overlay.pos.custom.hint"))
        current = cfg.get("position", "bottom-right")
        idx = POSITIONS.index(current) if current in POSITIONS else 5
        pos_combo.setCurrentIndex(idx)
        pos_combo.currentIndexChanged.connect(
            lambda i: self._app.set_overlay_option(
                "position", pos_combo.itemData(i)))
        form.addRow(tr("settings.actions.overlay.position"), pos_combo)

        hover_cb = QCheckBox(tr("settings.actions.overlay.hover_hide"))
        hover_cb.setChecked(bool(cfg.get("hover_hide", False)))
        hover_cb.toggled.connect(
            lambda v: self._app.set_overlay_option("hover_hide", v))
        hover_row = QHBoxLayout()
        hover_row.setContentsMargins(0, 0, 0, 0)
        hover_row.setSpacing(6)
        hover_row.addWidget(hover_cb)
        hover_row.addWidget(
            HintIcon(tr("settings.actions.overlay.hover_hide.hint")))
        hover_row.addStretch(1)
        form.addRow("", hover_row)

        from PySide6.QtWidgets import QSpinBox
        font_spin = QSpinBox()
        font_spin.setRange(8, 32)
        font_spin.setSuffix(" px")
        font_spin.setValue(int(cfg.get("font_size", 12)))
        font_spin.valueChanged.connect(
            lambda v: self._app.set_overlay_option("font_size", v))
        form.addRow(
            label_with_hint(tr("settings.actions.overlay.font_size"),
                            tr("settings.actions.overlay.font_size.hint")),
            font_spin)
        overlay_section.content_layout.addLayout(form)

        layout.addWidget(overlay_section)
        layout.addStretch()
        return self._scrollable(widget)

    # ------------------------------------------------------------------
    # Window state
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        pass  # TODO: save/restore window size and position via app config

    def showEvent(self, event) -> None:  # type: ignore[override]
        # Windows paints the native (white) background before Qt's first dark
        # frame – on a cold start that white flash looks like a stray window.
        # Show fully transparent and fade in right after the first paint.
        if not getattr(self, "_first_shown", False):
            self._first_shown = True
            self.setWindowOpacity(0.0)
            QTimer.singleShot(90, lambda: self.setWindowOpacity(1.0))
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        event.accept()
        # A preview is a "while I'm adjusting this" aid – it must never survive
        # the settings window.  Relying on each page's hideEvent alone is
        # fragile (it depends on which page happened to be open, and on Qt's
        # hide-order during teardown), so force every preview off explicitly
        # here as well; the publishes are harmless when nothing is showing.
        self._reset_previews()
        bus.publish("gui.settings_closed")

    def _reset_previews(self) -> None:
        """Turn every preview overlay off (see closeEvent)."""
        cb = getattr(self, "_chip_preview_cb", None)
        if cb is not None:
            try:
                if cb.isChecked():
                    cb.setChecked(False)
            except RuntimeError:
                pass          # widget already destroyed by a rebuild
        bus.publish("macros.preview", active=False)
        bus.publish("keyboard.preview", active=False)
