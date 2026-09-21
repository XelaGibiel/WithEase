"""Mouse module settings page."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from withease.core.i18n import tr
from withease.gui import theme
from withease.gui.widgets.collapsible_section import CollapsibleSection
from withease.gui.widgets.hotkey_edit import HotkeyEdit
from withease.gui.widgets.reset_field import ResetField
from withease.gui.widgets.sub_settings import SubSettings, group_heading
from withease.gui.widgets.value_slider import ValueSlider
from withease.gui.ui_utils import (checkbox_with_hint, label_with_hint,
                                  set_option_hint)
from withease.gui.widgets.screen_zone_overlay import ScreenZoneOverlay

_GRIDS = [("1×2", "1x2", 1, 2), ("2×2", "2x2", 2, 2), ("3×3", "3x3", 3, 3)]

if TYPE_CHECKING:
    from withease.modules.mouse import MouseModule


class MouseSettingsWidget(QWidget):
    def __init__(self, module: "MouseModule", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._settings = module._settings
        self._zone_overlay: ScreenZoneOverlay | None = None
        self._build_ui()
        from withease.gui.settings.module_sync import sync_module_checkbox
        sync_module_checkbox(self, module, self._enabled_cb,
                             self._update_enabled_state)
        # The zone preview must not outlive the settings window (hideEvent
        # alone doesn't cover every teardown order) – see MainWindow.closeEvent.
        from withease.core.event_bus import bus
        bus.subscribe("gui.settings_closed", self._on_settings_closed)
        self.destroyed.connect(
            lambda: bus.unsubscribe("gui.settings_closed",
                                    self._on_settings_closed))

    def _on_settings_closed(self, **_: object) -> None:
        self._hide_zone_overlay()
        self._drop_free_area()
        try:
            if self._zones_preview_cb.isChecked():
                self._zones_preview_cb.setChecked(False)
        except RuntimeError:
            pass              # widget already destroyed by a rebuild

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # Never scroll sideways (see MainWindow._scrollable): a page scrolled
        # right hid the cards' left edge behind the sidebar.
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Module toggle ────────────────────────────────────────────
        self._enabled_cb = QCheckBox(tr("module.mouse.enabled"))
        # Anywhere on the row switches the module, not only the box.
        from withease.gui.ui_utils import whole_row_toggle
        whole_row_toggle(self._enabled_cb)
        self._enabled_cb.setChecked(self._module.enabled)
        self._enabled_cb.setStyleSheet(theme.title_style())
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── Centering ────────────────────────────────────────────────
        self._centering_sec = CollapsibleSection(
            tr("module.mouse.centering"),
            self._settings.get("centering_enabled", False),
            description=tr("module.mouse.centering.description"),
            icon="🎯",
        )
        self._centering_sec.toggled.connect(
            lambda v: self._save("centering_enabled", v))

        centering_form = QFormLayout()
        centering_form.setSpacing(8)

        self._centering_delay = QSpinBox()
        self._centering_delay.setRange(1, 300)
        self._centering_delay.setSuffix(" s")
        self._centering_delay.setValue(int(self._settings.get("centering_delay", 5)))
        self._centering_delay.valueChanged.connect(self._on_centering_delay_changed)
        centering_form.addRow(tr("module.mouse.centering.delay"), self._centering_delay)

        self._centering_countdown = QSpinBox()
        # The countdown happens WITHIN the wait, so it can never exceed the
        # delay – cap its maximum at the current delay value.
        self._centering_countdown.setRange(0, 30)
        self._centering_countdown.setSuffix(" s")
        self._centering_countdown.setValue(
            int(self._settings.get("centering_countdown", 3)))
        self._centering_countdown.valueChanged.connect(
            lambda v: self._save("centering_countdown", v))
        centering_form.addRow(
            label_with_hint(tr("module.mouse.centering.countdown"),
                            tr("module.mouse.centering.countdown.hint")),
            self._centering_countdown)
        self._clamp_countdown_max()

        self._centering_hotkey = HotkeyEdit(
            self._settings.get("centering_hotkey", ""), action_id="mouse.center")
        self._centering_hotkey.key_changed.connect(
            lambda k: self._save("centering_hotkey", k))
        centering_form.addRow(tr("module.mouse.centering.hotkey"),
                              self._centering_hotkey)

        self._centering_symbol_cb = QCheckBox(tr("module.mouse.show_symbol"))
        self._centering_symbol_cb.setChecked(
            bool(self._settings.get("centering_show_indicator", True)))
        self._centering_symbol_cb.toggled.connect(
            lambda v: self._save("centering_show_indicator", v))
        centering_form.addRow("", checkbox_with_hint(
            self._centering_symbol_cb, tr("module.mouse.show_symbol.hint")))

        centering_form_widget = QWidget()
        centering_form_widget.setLayout(centering_form)
        self._centering_sec.content_layout.addWidget(centering_form_widget)
        layout.addWidget(self._centering_sec)

        # ── Precision mode ───────────────────────────────────────────
        self._precision_sec = CollapsibleSection(
            tr("module.mouse.precision"),
            self._settings.get("precision_mode_enabled", False),
            description=tr("module.mouse.precision.description"),
            icon="🐌",
        )
        self._precision_sec.toggled.connect(self._on_precision_toggled)

        precision_form = QFormLayout()
        precision_form.setSpacing(8)

        self._precision_mode_combo = QComboBox()
        self._precision_mode_combo.addItem(
            tr("module.mouse.precision.mode.hold"), "hold")
        self._precision_mode_combo.addItem(
            tr("module.mouse.precision.mode.toggle"), "toggle")
        for i, key in enumerate(("hold", "toggle")):
            set_option_hint(self._precision_mode_combo, i,
                            tr(f"module.mouse.precision.mode.{key}.hint"))
        current_mode = self._settings.get("precision_mode_type", "hold")
        self._precision_mode_combo.setCurrentIndex(
            0 if current_mode == "hold" else 1)
        self._precision_mode_combo.currentIndexChanged.connect(
            lambda i: self._save(
                "precision_mode_type",
                self._precision_mode_combo.itemData(i)))
        precision_form.addRow(
            label_with_hint(tr("module.mouse.precision.mode"),
                            tr("module.mouse.precision.mode.hint")),
            self._precision_mode_combo)

        self._precision_slider = ValueSlider(1, 10)
        self._precision_slider.setValue(
            int(self._settings.get("precision_speed", 3)))
        self._precision_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._precision_slider.setTickInterval(1)
        self._precision_slider.valueChanged.connect(
            lambda v: self._save("precision_speed", v))
        precision_form.addRow(tr("module.mouse.precision.speed"),
                              self._precision_slider)

        self._precision_hotkey = HotkeyEdit(
            self._settings.get("precision_hotkey", ""),
            action_id="mouse.precision_toggle")
        self._precision_hotkey.key_changed.connect(
            lambda k: self._save("precision_hotkey", k))
        precision_form.addRow(tr("module.mouse.precision.hotkey"),
                              self._precision_hotkey)

        self._precision_symbol_cb = QCheckBox(tr("module.mouse.show_symbol"))
        self._precision_symbol_cb.setChecked(
            bool(self._settings.get("precision_show_indicator", True)))
        self._precision_symbol_cb.toggled.connect(
            lambda v: self._save("precision_show_indicator", v))
        precision_form.addRow("", checkbox_with_hint(
            self._precision_symbol_cb, tr("module.mouse.show_symbol.hint")))

        precision_form_widget = QWidget()
        precision_form_widget.setLayout(precision_form)
        self._precision_sec.content_layout.addWidget(precision_form_widget)
        layout.addWidget(self._precision_sec)

        # ── Click-Lock ───────────────────────────────────────────────
        self._clicklock_sec = CollapsibleSection(
            tr("module.mouse.click_lock"),
            self._settings.get("click_lock_enabled", False),
            description=tr("module.mouse.click_lock.description"),
            icon="🔒",
        )
        self._clicklock_sec.toggled.connect(
            lambda v: self._save("click_lock_enabled", v))

        clicklock_form = QFormLayout()
        clicklock_form.setSpacing(8)

        self._clicklock_hotkey = HotkeyEdit(
            self._settings.get("clicklock_hotkey", ""),
            action_id="mouse.click_lock_toggle")
        self._clicklock_hotkey.key_changed.connect(
            lambda k: self._save("clicklock_hotkey", k))
        clicklock_form.addRow(tr("module.mouse.click_lock.hotkey"),
                              self._clicklock_hotkey)

        self._clicklock_symbol_cb = QCheckBox(tr("module.mouse.show_symbol"))
        self._clicklock_symbol_cb.setChecked(
            bool(self._settings.get("click_lock_show_indicator", True)))
        self._clicklock_symbol_cb.toggled.connect(
            lambda v: self._save("click_lock_show_indicator", v))
        clicklock_form.addRow("", checkbox_with_hint(
            self._clicklock_symbol_cb, tr("module.mouse.show_symbol.hint")))

        clicklock_form_widget = QWidget()
        clicklock_form_widget.setLayout(clicklock_form)
        self._clicklock_sec.content_layout.addWidget(clicklock_form_widget)
        layout.addWidget(self._clicklock_sec)

        # ── Cursor highlight ─────────────────────────────────────────
        self._highlight_sec = CollapsibleSection(
            tr("module.mouse.highlight"),
            self._settings.get("highlight_enabled", False),
            description=tr("module.mouse.highlight.description"),
            icon="✨",
        )
        self._highlight_sec.toggled.connect(
            lambda v: self._save("highlight_enabled", v))

        highlight_form = QFormLayout()
        highlight_form.setSpacing(8)
        self._highlight_form = highlight_form

        highlight_form.addRow(group_heading(tr("module.mouse.highlight.group.when")))
        self._highlight_hotkey = HotkeyEdit(
            self._settings.get("highlight_hotkey", ""), action_id="mouse.highlight")
        self._highlight_hotkey.key_changed.connect(
            lambda k: self._save("highlight_hotkey", k))
        highlight_form.addRow(tr("module.mouse.highlight.hotkey"),
                              self._highlight_hotkey)

        # Automatically: the first movement after the pointer stood still
        self._highlight_auto_cb = QCheckBox(tr("module.mouse.highlight.auto"))
        self._highlight_auto_cb.setChecked(
            bool(self._settings.get("highlight_auto", False)))
        self._highlight_auto_cb.toggled.connect(self._on_auto_toggled)
        highlight_form.addRow("", checkbox_with_hint(
            self._highlight_auto_cb, tr("module.mouse.highlight.auto.hint")))
        self._auto_sub = SubSettings()
        highlight_form.addRow("", self._auto_sub)

        self._highlight_auto_delay = QDoubleSpinBox()
        self._highlight_auto_delay.setRange(1.0, 60.0)
        self._highlight_auto_delay.setSingleStep(0.5)
        self._highlight_auto_delay.setDecimals(1)
        self._highlight_auto_delay.setSuffix(" s")
        self._highlight_auto_delay.setValue(
            float(self._settings.get("highlight_auto_delay", 3.0)))
        self._highlight_auto_delay.valueChanged.connect(
            lambda v: self._save("highlight_auto_delay", round(v, 1)))
        self._auto_sub.form.addRow(
            label_with_hint(tr("module.mouse.highlight.auto_delay"),
                            tr("module.mouse.highlight.auto_delay.hint")),
            self._reset_spin(self._highlight_auto_delay, 3.0, " s"))

        self._highlight_auto_free = ValueSlider(0, 50, suffix=" %", step=5)
        self._highlight_auto_free.setValue(
            int(self._settings.get("highlight_auto_free", 25)))
        self._highlight_auto_free.setTickPosition(
            QSlider.TickPosition.TicksBelow)
        self._highlight_auto_free.setTickInterval(5)
        self._highlight_auto_free.valueChanged.connect(
            lambda v: self._save("highlight_auto_free", v))
        # While the slider moves, the free area is drawn on the screen; it
        # goes again shortly after the slider is let go.
        self._free_overlay = None
        self._highlight_auto_free.slider.sliderPressed.connect(
            self._show_free_area)
        self._highlight_auto_free.valueChanged.connect(self._show_free_area)
        self._highlight_auto_free.slider.sliderReleased.connect(
            self._hide_free_area_soon)
        self._auto_sub.form.addRow(
            label_with_hint(tr("module.mouse.highlight.auto_free"),
                            tr("module.mouse.highlight.auto_free.hint")),
            self._reset_slider(self._highlight_auto_free, 25))

        # Pulsing rings toggle
        highlight_form.addRow(group_heading(tr("module.mouse.highlight.group.look")))
        self._highlight_rings_cb = QCheckBox(
            tr("module.mouse.highlight.rings"))
        self._highlight_rings_cb.setChecked(
            bool(self._settings.get("highlight_rings", True)))
        self._highlight_rings_cb.toggled.connect(self._on_rings_toggled)
        highlight_form.addRow("", self._highlight_rings_cb)
        self._rings_sub = SubSettings()
        highlight_form.addRow("", self._rings_sub)

        # Ring style: open (like the WithEase logo) or a closed circle.
        self._highlight_ring_style = QComboBox()
        self._highlight_ring_style.addItem(
            tr("module.mouse.highlight.ring_style.open"), "open")
        self._highlight_ring_style.addItem(
            tr("module.mouse.highlight.ring_style.closed"), "closed")
        for i, key in enumerate(("open", "closed")):
            set_option_hint(self._highlight_ring_style, i,
                            tr(f"module.mouse.highlight.ring_style.{key}.hint"))
        if self._settings.get("highlight_ring_style", "open") == "closed":
            self._highlight_ring_style.setCurrentIndex(1)
        self._highlight_ring_style.currentIndexChanged.connect(
            lambda i: self._save("highlight_ring_style",
                                 self._highlight_ring_style.itemData(i)))
        self._rings_sub.form.addRow(
            tr("module.mouse.highlight.ring_style"),
            self._reset_combo(self._highlight_ring_style, "open"))

        # Colour picker (applies to rings)
        self._highlight_color = list(
            self._settings.get("highlight_color", [255, 140, 0]))
        from withease.gui.ui_utils import em
        self._highlight_color_btn = QPushButton()
        self._highlight_color_btn.setFixedWidth(max(80, em(5)))
        self._update_color_button()
        self._highlight_color_btn.clicked.connect(self._pick_highlight_color)
        self._color_reset = ResetField(
            self._highlight_color_btn, [255, 140, 0],
            lambda: list(self._highlight_color), self._set_highlight_color,
            describe=lambda c: "#%02X%02X%02X" % tuple(c))
        self._rings_sub.form.addRow(tr("module.mouse.highlight.color"),
                                    self._color_reset)

        # Pulse radius
        self._highlight_radius = ValueSlider(30, 210, suffix=" px", step=20)
        self._highlight_radius.setValue(
            int(self._settings.get("highlight_radius", 90)))
        self._highlight_radius.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._highlight_radius.setTickInterval(20)       # every position
        self._highlight_radius.valueChanged.connect(
            lambda v: self._save("highlight_radius", v))
        self._rings_sub.form.addRow(tr("module.mouse.highlight.radius"),
                                    self._reset_slider(self._highlight_radius, 90))

        # Pulse duration
        self._highlight_duration = QDoubleSpinBox()
        self._highlight_duration.setRange(0.5, 10.0)
        self._highlight_duration.setSingleStep(0.1)
        self._highlight_duration.setDecimals(1)
        self._highlight_duration.setSuffix(" s")
        self._highlight_duration.setValue(
            float(self._settings.get("highlight_duration", 1.6)))
        self._highlight_duration.valueChanged.connect(
            lambda v: self._save("highlight_duration", round(v, 1)))
        self._rings_sub.form.addRow(
            tr("module.mouse.highlight.duration"),
            self._reset_spin(self._highlight_duration, 1.6, " s"))

        # Direction arrow toggle
        self._highlight_arrow_cb = QCheckBox(
            tr("module.mouse.highlight.arrow"))
        self._highlight_arrow_cb.setChecked(
            bool(self._settings.get("highlight_arrow", False)))
        self._highlight_arrow_cb.toggled.connect(self._on_arrow_toggled)
        highlight_form.addRow("", self._highlight_arrow_cb)
        self._arrow_sub = SubSettings()
        highlight_form.addRow("", self._arrow_sub)

        # Arrow thickness
        self._highlight_arrow_thickness = ValueSlider(3, 30, suffix=" px",
                                                      step=3)
        self._highlight_arrow_thickness.setValue(
            int(self._settings.get("highlight_arrow_thickness", 6)))
        self._highlight_arrow_thickness.setTickPosition(
            QSlider.TickPosition.TicksBelow)
        self._highlight_arrow_thickness.setTickInterval(3)
        self._highlight_arrow_thickness.valueChanged.connect(
            lambda v: self._save("highlight_arrow_thickness", v))
        self._arrow_thickness_row_label = QLabel(
            tr("module.mouse.highlight.arrow_thickness"))
        self._arrow_sub.form.addRow(
            self._arrow_thickness_row_label,
            self._reset_slider(self._highlight_arrow_thickness, 6))

        # Permanent direction arrow (corner overlay pointing at the cursor)
        highlight_form.addRow(
            group_heading(tr("module.mouse.highlight.group.always")))
        self._arrow_persistent_cb = QCheckBox(
            tr("module.mouse.highlight.arrow_persistent"))

        self._arrow_persistent_cb.setChecked(
            bool(self._settings.get("highlight_arrow_persistent", False)))
        self._arrow_persistent_cb.toggled.connect(self._on_persistent_arrow_toggled)
        highlight_form.addRow("", checkbox_with_hint(
            self._arrow_persistent_cb,
            tr("module.mouse.highlight.arrow_persistent.hint")))
        self._persist_sub = SubSettings()
        highlight_form.addRow("", self._persist_sub)

        self._arrow_corner = QComboBox()
        for value, key in (("top-left", "top_left"), ("top-right", "top_right"),
                           ("bottom-left", "bottom_left"),
                           ("bottom-right", "bottom_right")):
            self._arrow_corner.addItem(
                tr(f"module.mouse.highlight.corner.{key}"), value)
        cur = self._settings.get("highlight_arrow_corner", "bottom-right")
        idx = self._arrow_corner.findData(cur)
        self._arrow_corner.setCurrentIndex(idx if idx >= 0 else 3)
        self._arrow_corner.currentIndexChanged.connect(
            lambda i: self._save("highlight_arrow_corner",
                                 self._arrow_corner.itemData(i)))
        self._arrow_corner_label = QLabel(tr("module.mouse.highlight.corner"))
        self._persist_sub.form.addRow(
            self._arrow_corner_label,
            self._reset_combo(self._arrow_corner, "bottom-right"))

        self._arrow_size = ValueSlider(24, 132, suffix=" px", step=12)
        self._arrow_size.setValue(int(self._settings.get("highlight_arrow_size", 48)))
        self._arrow_size.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._arrow_size.setTickInterval(12)
        self._arrow_size.valueChanged.connect(
            lambda v: self._save("highlight_arrow_size", v))
        self._arrow_size_label = QLabel(tr("module.mouse.highlight.arrow_size"))
        self._persist_sub.form.addRow(self._arrow_size_label,
                                      self._reset_slider(self._arrow_size, 48))

        # Permanent, lightly translucent circle around the cursor (always on)
        self._circle_cb = QCheckBox(tr("module.mouse.highlight.circle"))

        self._circle_cb.setChecked(
            bool(self._settings.get("highlight_permanent_circle", False)))
        self._circle_cb.toggled.connect(self._on_circle_toggled)
        highlight_form.addRow("", checkbox_with_hint(
            self._circle_cb, tr("module.mouse.highlight.circle.hint")))
        self._circle_sub = SubSettings()
        highlight_form.addRow("", self._circle_sub)

        self._circle_radius = ValueSlider(20, 120, suffix=" px", step=10)
        self._circle_radius.setValue(
            int(self._settings.get("highlight_circle_radius", 40)))
        self._circle_radius.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._circle_radius.setTickInterval(10)
        self._circle_radius.valueChanged.connect(
            lambda v: self._save("highlight_circle_radius", v))
        self._circle_radius_label = QLabel(
            tr("module.mouse.highlight.circle_radius"))
        self._circle_sub.form.addRow(self._circle_radius_label,
                                     self._reset_slider(self._circle_radius, 40))

        self._circle_opacity = ValueSlider(5, 95, suffix=" %", step=10)
        self._circle_opacity.setValue(
            int(self._settings.get("highlight_circle_opacity", 25)))
        self._circle_opacity.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._circle_opacity.setTickInterval(10)
        self._circle_opacity.valueChanged.connect(
            lambda v: self._save("highlight_circle_opacity", v))
        self._circle_opacity_label = QLabel(
            tr("module.mouse.highlight.circle_opacity"))
        self._circle_sub.form.addRow(
            self._circle_opacity_label,
            self._reset_slider(self._circle_opacity, 25))

        # No preview button - every change to the look shows the highlight
        # right away (see _connect_live_preview) - and no reset-everything
        # button: every setting has its own ↺ behind it.

        highlight_form_widget = QWidget()
        highlight_form_widget.setLayout(highlight_form)
        self._highlight_sec.content_layout.addWidget(highlight_form_widget)
        layout.addWidget(self._highlight_sec)
        # Initial row visibility – only AFTER the form is parented: calling
        # setRowVisible(True) on parentless rows briefly shows them as
        # stray top-level windows (visible as flicker on rebuilds).
        self._on_rings_toggled(self._highlight_rings_cb.isChecked())
        self._on_arrow_toggled(self._highlight_arrow_cb.isChecked())
        self._on_persistent_arrow_toggled(
            self._arrow_persistent_cb.isChecked())
        self._on_circle_toggled(self._circle_cb.isChecked())
        self._on_auto_toggled(self._highlight_auto_cb.isChecked())
        self._connect_live_preview()

        # ── Keyboard as mouse buttons ────────────────────────────────
        self._kbclick_sec = CollapsibleSection(
            tr("module.mouse.keyboard_clicks"),
            self._settings.get("keyboard_clicks_enabled", False),
            description=tr("module.mouse.keyboard_clicks.description"),
            icon="⌨️",
        )
        self._kbclick_sec.toggled.connect(
            lambda v: self._save("keyboard_clicks_enabled", v))

        kbclick_form = QFormLayout()
        kbclick_form.setSpacing(8)

        self._kb_left = HotkeyEdit(self._settings.get("keyboard_click_left", ""))
        self._kb_left.key_changed.connect(
            lambda k: self._save("keyboard_click_left", k))
        kbclick_form.addRow(tr("module.mouse.keyboard_clicks.left"), self._kb_left)

        self._kb_right = HotkeyEdit(self._settings.get("keyboard_click_right", ""))
        self._kb_right.key_changed.connect(
            lambda k: self._save("keyboard_click_right", k))
        kbclick_form.addRow(tr("module.mouse.keyboard_clicks.right"), self._kb_right)

        self._kb_double = HotkeyEdit(self._settings.get("keyboard_click_double", ""))
        self._kb_double.key_changed.connect(
            lambda k: self._save("keyboard_click_double", k))
        kbclick_form.addRow(tr("module.mouse.keyboard_clicks.double"), self._kb_double)

        kbclick_form_widget = QWidget()
        kbclick_form_widget.setLayout(kbclick_form)
        self._kbclick_sec.content_layout.addWidget(kbclick_form_widget)
        layout.addWidget(self._kbclick_sec)

        # ── Screen zones ─────────────────────────────────────────────
        self._zones_sec = CollapsibleSection(
            tr("module.mouse.screen_zones"),
            self._settings.get("screen_zones_enabled", False),
            description=tr("module.mouse.screen_zones.description"),
            icon="🗺️",
        )
        self._zones_sec.toggled.connect(
            lambda v: self._save("screen_zones_enabled", v))

        self._zones_sec.content_layout.addWidget(
            QLabel(tr("module.mouse.screen_zones.hint")))

        # Grid size selector
        grid_form = QFormLayout()
        grid_form.setSpacing(6)
        self._grid_combo = QComboBox()
        for label, key, _r, _c in _GRIDS:
            self._grid_combo.addItem(label, key)
        saved_grid = self._settings.get("screen_zones_grid", "3x3")
        grid_keys = [g[1] for g in _GRIDS]
        self._grid_combo.setCurrentIndex(
            grid_keys.index(saved_grid) if saved_grid in grid_keys else 2)
        self._grid_combo.currentIndexChanged.connect(self._on_grid_changed)
        grid_form.addRow(
            label_with_hint(tr("module.mouse.screen_zones.grid"),
                            tr("module.mouse.screen_zones.grid.hint")),
            self._grid_combo)
        grid_form_widget = QWidget()
        grid_form_widget.setLayout(grid_form)
        self._zones_sec.content_layout.addWidget(grid_form_widget)

        self._zones_preview_cb = QCheckBox(tr("module.mouse.screen_zones.preview"))
        self._zones_preview_cb.toggled.connect(self._on_zone_preview_toggled)
        self._zones_sec.content_layout.addWidget(self._zones_preview_cb)

        # Zone hotkey grid (rebuilt on grid change)
        self._zone_grid_container = QWidget()
        self._zone_grid_layout = QVBoxLayout(self._zone_grid_container)
        self._zone_grid_layout.setContentsMargins(0, 0, 0, 0)
        self._zones_sec.content_layout.addWidget(self._zone_grid_container)
        self._rebuild_zone_grid()

        layout.addWidget(self._zones_sec)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._sections = [
            self._centering_sec, self._precision_sec, self._clicklock_sec,
            self._highlight_sec, self._kbclick_sec, self._zones_sec,
        ]
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _on_centering_delay_changed(self, value: int) -> None:
        self._save("centering_delay", float(value))
        # Keep the countdown ≤ the (new) delay; the spin box clamps its value
        # automatically and emits valueChanged, which saves the clamped value.
        self._clamp_countdown_max()

    def _clamp_countdown_max(self) -> None:
        self._centering_countdown.setMaximum(
            min(30, int(self._centering_delay.value())))

    def _update_color_button(self) -> None:
        r, g, b = self._highlight_color
        # Pick readable text colour based on perceived brightness
        text = "#000000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#ffffff"
        self._highlight_color_btn.setText(f"#{r:02X}{g:02X}{b:02X}")
        self._highlight_color_btn.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {text};")

    # -- one ↺ per setting ----------------------------------------------

    @staticmethod
    def _reset_slider(slider: ValueSlider, default: int) -> ResetField:
        suffix = slider._suffix
        return ResetField(slider, default, slider.value, slider.setValue,
                          slider.valueChanged,
                          describe=lambda v: f"{v}{suffix}")

    @staticmethod
    def _reset_spin(spin: Any, default: float, suffix: str) -> ResetField:
        from PySide6.QtCore import QLocale
        return ResetField(
            spin, default, lambda: round(spin.value(), 1), spin.setValue,
            spin.valueChanged,
            describe=lambda v: QLocale().toString(float(v), "f", 1) + suffix)

    @staticmethod
    def _reset_combo(combo: QComboBox, default: str) -> ResetField:
        return ResetField(
            combo, default, combo.currentData,
            lambda v: combo.setCurrentIndex(max(0, combo.findData(v))),
            combo.currentIndexChanged,
            describe=lambda v: combo.itemText(max(0, combo.findData(v))))

    def _set_highlight_color(self, color: list[int]) -> None:
        self._highlight_color = list(color)
        self._update_color_button()
        self._save("highlight_color", self._highlight_color)
        if hasattr(self, "_color_reset"):
            self._color_reset.refresh()
        if hasattr(self, "_preview_timer"):
            self._preview_highlight()           # see the colour at once

    def _pick_highlight_color(self) -> None:
        r, g, b = self._highlight_color
        chosen = QColorDialog.getColor(
            QColor(r, g, b), self, tr("module.mouse.highlight.color"))
        if chosen.isValid():
            self._set_highlight_color(
                [chosen.red(), chosen.green(), chosen.blue()])

    def _on_rings_toggled(self, enabled: bool) -> None:
        self._save("highlight_rings", enabled)
        # Style, colour, radius and duration only apply to the rings (the
        # duration is how long ONE ring pulse lasts)
        self._highlight_form.setRowVisible(self._rings_sub, enabled)
        # Keep at least one visible cue – otherwise the highlight shows nothing.
        if not enabled and not self._highlight_arrow_cb.isChecked():
            self._highlight_arrow_cb.setChecked(True)

    def _on_arrow_toggled(self, enabled: bool) -> None:
        self._save("highlight_arrow", enabled)
        # Thickness slider is only shown when the arrow is enabled
        self._highlight_form.setRowVisible(self._arrow_sub, enabled)
        if not enabled and not self._highlight_rings_cb.isChecked():
            self._highlight_rings_cb.setChecked(True)

    def _on_persistent_arrow_toggled(self, enabled: bool) -> None:
        self._save("highlight_arrow_persistent", enabled)
        # Corner + size only make sense when the permanent arrow is on.
        self._highlight_form.setRowVisible(self._persist_sub, enabled)

    def _show_free_area(self, *_: object) -> None:
        from withease.gui.widgets.free_area_overlay import FreeAreaOverlay
        if self._free_overlay is None:
            self._free_overlay = FreeAreaOverlay()
        self._free_overlay.show_percent(self._highlight_auto_free.value(),
                                        near=self)
        # a key press or the wheel has no "let go" - it hides by itself
        if not self._highlight_auto_free.slider.isSliderDown():
            self._free_overlay.hide_soon()

    def _hide_free_area_soon(self) -> None:
        if self._free_overlay is not None:
            self._free_overlay.hide_soon()

    def _drop_free_area(self) -> None:
        overlay, self._free_overlay = getattr(self, "_free_overlay", None), None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()

    def _on_auto_toggled(self, enabled: bool) -> None:
        self._save("highlight_auto", enabled)
        # how long still, and the free middle: only for the automatic mode
        self._highlight_form.setRowVisible(self._auto_sub, enabled)

    def _on_circle_toggled(self, enabled: bool) -> None:
        self._save("highlight_permanent_circle", enabled)
        # Radius + opacity only make sense when the permanent circle is on.
        self._highlight_form.setRowVisible(self._circle_sub, enabled)

    def _connect_live_preview(self) -> None:
        """Changing how the highlight looks shows it at once, with the new
        values - no separate preview button.  A short delay bundles a slider
        drag (or a reset changing several values) into one pulse."""
        from PySide6.QtCore import QTimer
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._preview_highlight)
        start = lambda *_: self._preview_timer.start()   # noqa: E731
        for slider in (self._highlight_radius, self._highlight_arrow_thickness):
            slider.valueChanged.connect(start)
            slider.slider.sliderPressed.connect(start)
        self._highlight_duration.valueChanged.connect(start)
        self._highlight_ring_style.currentIndexChanged.connect(start)
        self._highlight_rings_cb.toggled.connect(start)
        self._highlight_arrow_cb.toggled.connect(start)

    def _preview_highlight(self) -> None:
        from withease.core.event_bus import bus
        bus.publish("mouse.highlight",
                    rings=self._highlight_rings_cb.isChecked(),
                    ring_style=self._highlight_ring_style.currentData(),
                    color=self._highlight_color,
                    radius=self._highlight_radius.value(),
                    arrow=self._highlight_arrow_cb.isChecked(),
                    arrow_thickness=self._highlight_arrow_thickness.value(),
                    duration_ms=int(self._highlight_duration.value() * 1000))

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _on_precision_toggled(self, enabled: bool) -> None:
        self._settings["precision_mode_enabled"] = enabled
        if not enabled:
            self._module._disable_precision()
        self._module.on_settings_changed()

    def _on_grid_changed(self, index: int) -> None:
        _, key, _r, _c = _GRIDS[index]
        self._save("screen_zones_grid", key)
        self._rebuild_zone_grid()
        # Refresh overlay if visible
        if self._zones_preview_cb.isChecked():
            self._on_zone_preview_toggled(False)
            self._on_zone_preview_toggled(True)

    def _rebuild_zone_grid(self) -> None:
        # Remove previous grid widget (if any) without touching the persistent layout
        while self._zone_grid_layout.count():
            item = self._zone_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        _, _key, rows, cols = _GRIDS[self._grid_combo.currentIndex()]
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        zone_num = 1
        for row in range(rows):
            for col in range(cols):
                cell = QWidget()
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(4, 4, 4, 4)
                cell_layout.setSpacing(2)
                cell_layout.addWidget(QLabel(str(zone_num)))
                he = HotkeyEdit(
                    self._settings.get(f"screen_zone_{zone_num}_hotkey", ""),
                    action_id=f"mouse.zone_{zone_num}")
                he.key_changed.connect(
                    lambda k, n=zone_num: self._save(f"screen_zone_{n}_hotkey", k))
                cell_layout.addWidget(he)
                cell.setStyleSheet(
                    "QWidget { border: 1px solid palette(mid); border-radius: 4px; }")
                grid.addWidget(cell, row, col)
                zone_num += 1
        self._zone_grid_layout.addWidget(grid_widget)

    def _on_zone_preview_toggled(self, checked: bool) -> None:
        if checked:
            if self._zone_overlay is None:
                _, _key, rows, cols = _GRIDS[self._grid_combo.currentIndex()]
                self._zone_overlay = ScreenZoneOverlay(rows, cols)
            self._zone_overlay.show()
        else:
            self._hide_zone_overlay()

    def _hide_zone_overlay(self) -> None:
        if self._zone_overlay is not None:
            self._zone_overlay.hide()
            self._zone_overlay.deleteLater()
            self._zone_overlay = None

    def hideEvent(self, event: object) -> None:  # type: ignore[override]
        self._hide_zone_overlay()
        self._drop_free_area()
        if self._zones_preview_cb.isChecked():
            self._zones_preview_cb.setChecked(False)
        super().hideEvent(event)  # type: ignore[arg-type]

    def _update_enabled_state(self, enabled: bool) -> None:
        for sec in self._sections:
            sec.setEnabled(enabled)
