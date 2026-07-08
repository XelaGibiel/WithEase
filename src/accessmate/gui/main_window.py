"""Main settings window.

Uses a sidebar navigation on the left and a stacked widget on the right.
Every module provides its own settings widget via get_settings_widget().
The window is fully keyboard-navigable (Tab order, keyboard shortcuts).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from accessmate import __version__
from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr, SUPPORTED_LANGUAGES
from accessmate.core import i18n as i18n_module
from accessmate.gui import theme

if TYPE_CHECKING:
    from accessmate.app import AccessMateApp


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
        try:
            self._anim.finished.disconnect()
        except RuntimeError:
            pass
        self._anim.setDuration(self._FADE_IN_MS)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hold_timer.start()  # restart hold on every save

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(self._FADE_OUT_MS)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        try:
            self._anim.finished.disconnect()
        except RuntimeError:
            pass
        self._anim.finished.connect(self._on_faded)
        self._anim.start()

    def _on_faded(self) -> None:
        try:
            self._anim.finished.disconnect()
        except RuntimeError:
            pass
        if self._effect.opacity() < 0.05:
            self.hide()


class MainWindow(QMainWindow):
    def __init__(self, app: "AccessMateApp") -> None:
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
        from accessmate.gui.ui_utils import em
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        self._sidebar = sidebar
        sidebar.setFixedWidth(max(190, em(11)))
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
        # The list is sized to its content (no scrollbars, no oversized frame).
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setStyleSheet(theme.selection_qss("QListWidget#nav"))
        sidebar_layout.addWidget(self._nav)

        sidebar_layout.addStretch()

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

        # ---- Content area ----
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # ---- Footer: active profile (left), version/update (right) ----
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 4, 12, 4)

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
        outer.addLayout(footer)

        self._latest_release = None
        self._start_update_check()
        self._update_footer_profile()

        self._populate_nav()
        from accessmate.gui.ui_utils import compact_fields
        compact_fields(self._stack)
        self._nav.currentItemChanged.connect(self._on_nav_changed)
        self._select_nav_row(0)

    def _on_nav_changed(self, current, _previous) -> None:
        if current is None:
            return
        page_index = current.data(Qt.ItemDataRole.UserRole)
        if page_index is not None:
            self._stack.setCurrentIndex(page_index)

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
        whether AccessMate is currently active."""
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
        from accessmate.core import updater
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
        from accessmate.gui.update_dialog import UpdateDialog
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

        from accessmate.gui.settings.store_page import StorePage
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

        self._fit_nav_height()

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
        from accessmate.gui.ui_utils import em
        item = QListWidgetItem(label)
        item.setSizeHint(item.sizeHint().__class__(160, max(36, em(2))))
        item.setData(Qt.ItemDataRole.UserRole, self._stack.count())
        self._nav.addItem(item)
        self._stack.addWidget(widget)

    def _add_nav_separator(self) -> None:
        # Invisible spacer between core modules and add-ons: pure spacing,
        # no drawn line (in any theme) – the gap alone groups the entries.
        from accessmate.gui.ui_utils import em
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)  # not selectable, no page
        item.setSizeHint(item.sizeHint().__class__(160, max(11, em(0.6))))
        self._nav.addItem(item)

    def _fit_nav_height(self) -> None:
        """Size the list exactly to its rows – no scrollbar, no excess frame."""
        total = sum(self._nav.sizeHintForRow(i) for i in range(self._nav.count()))
        self._nav.setFixedHeight(total + 2 * self._nav.frameWidth() + 4)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @staticmethod
    def _page_title(text: str) -> QLabel:
        """Page heading – same size/weight as the module enable-checkboxes,
        so all sidebar pages start with a uniform title."""
        label = QLabel(text)
        label.setStyleSheet(theme.title_style())
        return label

    def _build_general_page(self) -> QWidget:
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(24, 24, 24, 24)

        title = self._page_title(tr("settings.general.title"))
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

        # Theme selector
        self._theme_combo = QComboBox()
        self._theme_combo.setMinimumWidth(160)
        current_theme = self._app._app_config.get("theme", "system")
        for key in ("system", "light", "dark"):
            self._theme_combo.addItem(tr(f"settings.general.theme.{key}"), key)
            if key == current_theme:
                self._theme_combo.setCurrentIndex(self._theme_combo.count() - 1)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow(tr("settings.general.theme"), self._theme_combo)

        # Contrast level (accessibility)
        self._contrast_combo = QComboBox()
        self._contrast_combo.addItem(tr("settings.general.contrast.normal"),
                                     "normal")
        self._contrast_combo.addItem(tr("settings.general.contrast.high"),
                                     "high")
        if self._app._app_config.get("contrast", "normal") == "high":
            self._contrast_combo.setCurrentIndex(1)
        self._contrast_combo.currentIndexChanged.connect(
            self._on_contrast_changed)
        form.addRow(tr("settings.general.contrast"), self._contrast_combo)

        # Global font size (accessibility)
        from PySide6.QtWidgets import QSpinBox
        # 8–16 pt, or "system default".  The minimum (7) is never a real
        # size – it shows the special text and is stored as 0 (= system).
        self._font_spin = QSpinBox()
        self._font_spin.setRange(7, 16)
        self._font_spin.setSpecialValueText(
            tr("settings.general.font_size.system"))
        self._font_spin.setSuffix(" pt")
        saved_pt = int(self._app._app_config.get("font_size", 0))
        self._font_spin.setValue(saved_pt if 8 <= saved_pt <= 16 else 7)
        # Live preview: every change applies after a short pause (the pause
        # keeps typing "12" from briefly applying 1pt-clamped steps, and each
        # apply rebuilds the pages, which would steal the field mid-input).
        self._font_spin.valueChanged.connect(self._on_font_size_live)
        form.addRow(tr("settings.general.font_size"), self._font_spin)

        # Autostart with Windows
        from PySide6.QtWidgets import QCheckBox
        from accessmate.core import autostart
        self._autostart_cb = QCheckBox()
        self._autostart_cb.setChecked(autostart.is_enabled())
        self._autostart_cb.toggled.connect(self._on_autostart_toggled)
        form.addRow(tr("settings.general.autostart"), self._autostart_cb)

        # Emergency stop key
        from accessmate.gui.widgets.hotkey_edit import HotkeyEdit
        emergency = self._app._profile_data.get("emergency_key", "F12")
        if (emergency and "+" not in emergency
                and not (emergency.startswith("Key.")
                         or emergency.startswith("'"))):
            emergency = f"Key.{emergency.lower()}"
        self._emergency_edit = HotkeyEdit(emergency,
                                          action_id="app.emergency_stop")
        self._emergency_edit.key_changed.connect(self._app.set_emergency_key)

        form.addRow(tr("settings.general.emergency_key"),
                    self._emergency_edit)

        outer.addLayout(form)

        # Description as its own full-width row: a word-wrapping label
        # nested inside a form cell reports a wrong height and gets clipped
        # (Qt heightForWidth limitation) – as a top-level row it always
        # grows with its text, at any font size.
        emergency_desc = QLabel(tr("settings.general.emergency_key.description"))
        emergency_desc.setStyleSheet(theme.hint_style())
        emergency_desc.setWordWrap(True)
        from accessmate.gui.ui_utils import em
        emergency_desc.setMaximumWidth(em(36))
        outer.addSpacing(4)
        outer.addWidget(emergency_desc)
        outer.addStretch()
        return widget

    def _apply_appearance(self, key: str, value) -> None:
        """Persist one appearance setting and re-apply the whole theme
        (scheme + contrast + font size always travel together, otherwise
        changing one would silently reset the others)."""
        cfg = self._app._app_config
        cfg[key] = value
        from accessmate.core import config
        config.save_app_config(cfg)
        from accessmate.gui.theme import apply_theme
        apply_theme(self._app._qt_app, cfg.get("theme", "system"),
                    cfg.get("contrast", "normal"),
                    int(cfg.get("font_size", 0)))

    def _on_theme_changed(self, index: int) -> None:
        self._apply_appearance("theme", self._theme_combo.itemData(index))

    def _on_contrast_changed(self, index: int) -> None:
        self._apply_appearance(
            "contrast", self._contrast_combo.itemData(index))

    def _on_font_size_live(self, _value: int) -> None:
        if not hasattr(self, "_font_apply_timer"):
            self._font_apply_timer = QTimer(self)
            self._font_apply_timer.setSingleShot(True)
            self._font_apply_timer.setInterval(400)
            self._font_apply_timer.timeout.connect(self._on_font_size_changed)
        self._font_apply_timer.start()   # restart on every further change

    def _on_font_size_changed(self) -> None:
        value = int(self._font_spin.value())
        value = value if value >= 8 else 0   # minimum shows "system" = 0
        if value == int(self._app._app_config.get("font_size", 0)):
            return  # unchanged – no rebuild needed
        # Give the recreated spinbox focus again so arrow clicks / typing
        # can simply continue after the pages were rebuilt.
        self._refocus_font_spin = self._font_spin.hasFocus()
        self._apply_appearance("font_size", value)

    def _on_autostart_toggled(self, enabled: bool) -> None:
        from accessmate.core import autostart
        if not autostart.set_enabled(enabled):
            # Registry write failed – revert the checkbox silently.
            self._autostart_cb.blockSignals(True)
            self._autostart_cb.setChecked(autostart.is_enabled())
            self._autostart_cb.blockSignals(False)
            return
        self._app._app_config["autostart"] = enabled
        from accessmate.core import config
        config.save_app_config(self._app._app_config)

    def _on_language_changed(self, index: int) -> None:
        lang_code = self._lang_combo.itemData(index)
        i18n_module.load(lang_code)
        self._app._app_config["language"] = lang_code
        from accessmate.core import config
        config.save_app_config(self._app._app_config)
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Rebuild the entire window content (language or profile change)."""
        self._rebuilding = True
        try:
            self._do_rebuild_ui()
        finally:
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
        # that depends on theme or font size (selection colours, widths).
        self._nav.setStyleSheet(theme.selection_qss("QListWidget#nav"))
        from accessmate.gui.ui_utils import em
        self._sidebar.setFixedWidth(max(190, em(11)))
        self._populate_nav()
        from accessmate.gui.ui_utils import compact_fields
        compact_fields(self._stack)

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

        if getattr(self, "_refocus_font_spin", False):
            self._refocus_font_spin = False
            self._font_spin.setFocus()
            self._font_spin.selectAll()

    def _build_profiles_page(self) -> QWidget:
        from PySide6.QtWidgets import QListWidget

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self._page_title(tr("settings.profiles.title")))

        desc = QLabel(tr("settings.profiles.description"))
        desc.setStyleSheet(theme.hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._profiles_list = QListWidget()
        self._profiles_list.setMinimumHeight(160)
        self._profiles_list.setStyleSheet(theme.selection_qss("QListWidget"))
        self._profiles_list.doubleClicked.connect(
            lambda _: self._on_profile_activate())
        self._profiles_list.currentRowChanged.connect(
            lambda _: self._update_profile_buttons())
        layout.addWidget(self._profiles_list)

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
        self._profile_delete_btn.clicked.connect(self._on_profile_delete)
        btn_row.addWidget(self._profile_delete_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        self._refresh_profiles_list()
        return widget

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
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, tr("settings.profiles.delete"),
            tr("settings.profiles.delete.confirm", name=sel))
        if answer == QMessageBox.StandardButton.Yes:
            self._app.delete_profile(sel)
            self._refresh_profiles_list()

    def _build_actions_page(self) -> QWidget:
        from PySide6.QtWidgets import QCheckBox, QTableWidget, QTableWidgetItem
        from accessmate.gui.widgets.hotkey_edit import HotkeyEdit

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self._page_title(tr("settings.actions.title")))

        desc = QLabel(tr("settings.actions.overview_hint"))
        desc.setStyleSheet(theme.hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels([
            tr("settings.actions.col.favorite"),
            tr("settings.actions.action_col"),
            tr("settings.actions.trigger_col"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 60)
        table.setColumnWidth(1, 260)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(220)
        from PySide6.QtWidgets import QStyleFactory
        style = QStyleFactory.create("Fusion")
        if style is not None:
            table.setStyle(style)
        table.setStyleSheet(theme.selection_qss("QTableWidget"))

        # Rows: favourites first (in overlay order), then everything else.
        # Only ACTIVE entries are listed: actions whose tool is enabled have a
        # trigger assigned (disabled tools are gated to an empty trigger).
        from PySide6.QtWidgets import QLineEdit
        from accessmate.core.action_manager import action_manager

        search = QLineEdit()
        search.setPlaceholderText(tr("settings.actions.search"))
        search.setClearButtonEnabled(True)
        layout.addWidget(search)

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
                cell = QWidget()
                cell_layout = QHBoxLayout(cell)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.addWidget(cb)
                table.setCellWidget(row, 0, cell)
                label_item = QTableWidgetItem(label)
                label_item.setData(Qt.ItemDataRole.UserRole, fid)
                table.setItem(row, 1, label_item)
                table.setItem(row, 2, QTableWidgetItem(key))
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
        layout.addWidget(table)

        # ▲▼ reorder the favourite block at the top of the table.
        move_row = QHBoxLayout()
        move_hint = QLabel(tr("settings.actions.order_hint"))
        move_hint.setStyleSheet(theme.hint_style())
        move_row.addWidget(move_hint)
        move_row.addStretch()
        for arrow, delta in (("▲", -1), ("▼", 1)):
            from accessmate.gui.ui_utils import em
            btn = QPushButton(arrow)
            btn.setFixedWidth(max(32, em(2)))

            def on_move(_checked: bool = False, d: int = delta) -> None:
                fid = selected_fid()
                if fid and self._app.is_favorite(fid):
                    self._app.move_favorite(fid, d)
                    refresh_table(fid)

            btn.clicked.connect(on_move)
            move_row.addWidget(btn)
        layout.addLayout(move_row)

        # ── Overlay settings ─────────────────────────────────────────
        cfg = self._app.get_overlay_config()

        # The section title itself is the on/off checkbox.
        overlay_cb = QCheckBox(tr("settings.actions.overlay"))
        overlay_cb.setStyleSheet("font-weight: bold;")
        overlay_cb.setChecked(bool(cfg.get("enabled", False)))
        layout.addWidget(overlay_cb)

        overlay_desc = QLabel(tr("settings.actions.overlay.description"))
        overlay_desc.setStyleSheet(theme.hint_style())
        overlay_desc.setWordWrap(True)
        layout.addWidget(overlay_desc)

        # All further options live in one container that is only shown while
        # the overlay is enabled – they are meaningless otherwise.
        opts = QWidget()
        opts_layout = QVBoxLayout(opts)
        opts_layout.setContentsMargins(0, 0, 0, 0)
        opts_layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(8)

        from accessmate.gui.widgets.actions_overlay import POSITIONS
        pos_combo = QComboBox()
        for pos in POSITIONS:
            label = (tr("settings.actions.overlay.pos.custom")
                     if pos == "custom" else tr(f"keyboard.indicator.pos.{pos}"))
            pos_combo.addItem(label, pos)
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
        form.addRow("", hover_cb)

        from PySide6.QtWidgets import QSpinBox
        font_spin = QSpinBox()
        font_spin.setRange(8, 32)
        font_spin.setSuffix(" px")
        font_spin.setValue(int(cfg.get("font_size", 12)))
        font_spin.valueChanged.connect(
            lambda v: self._app.set_overlay_option("font_size", v))
        form.addRow(tr("settings.actions.overlay.font_size"), font_spin)
        opts_layout.addLayout(form)

        opts.setVisible(overlay_cb.isChecked())

        def on_overlay_toggled(enabled: bool) -> None:
            self._app.set_overlay_option("enabled", enabled)
            opts.setVisible(enabled)

        overlay_cb.toggled.connect(on_overlay_toggled)

        layout.addWidget(opts)
        layout.addStretch()
        return widget

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
        bus.publish("gui.settings_closed")
