"""Mouse module settings page."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
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

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Module toggle ────────────────────────────────────────────
        self._enabled_cb = QCheckBox(tr("module.mouse.enabled"))
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
        centering_form.addRow(tr("module.mouse.centering.countdown"),
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
        centering_form.addRow("", self._centering_symbol_cb)

        centering_form_widget = QWidget()
        centering_form_widget.setLayout(centering_form)
        self._centering_sec.content_layout.addWidget(centering_form_widget)
        layout.addWidget(self._centering_sec)

        # ── Precision mode ───────────────────────────────────────────
        self._precision_sec = CollapsibleSection(
            tr("module.mouse.precision"),
            self._settings.get("precision_mode_enabled", False),
            description=tr("module.mouse.precision.description"),
        )
        self._precision_sec.toggled.connect(self._on_precision_toggled)

        precision_form = QFormLayout()
        precision_form.setSpacing(8)

        self._precision_mode_combo = QComboBox()
        self._precision_mode_combo.addItem(
            tr("module.mouse.precision.mode.hold"), "hold")
        self._precision_mode_combo.addItem(
            tr("module.mouse.precision.mode.toggle"), "toggle")
        current_mode = self._settings.get("precision_mode_type", "hold")
        self._precision_mode_combo.setCurrentIndex(
            0 if current_mode == "hold" else 1)
        self._precision_mode_combo.currentIndexChanged.connect(
            lambda i: self._save(
                "precision_mode_type",
                self._precision_mode_combo.itemData(i)))
        precision_form.addRow(tr("module.mouse.precision.mode"),
                              self._precision_mode_combo)

        self._precision_slider = QSlider(Qt.Orientation.Horizontal)
        self._precision_slider.setRange(1, 10)
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
        precision_form.addRow("", self._precision_symbol_cb)

        precision_form_widget = QWidget()
        precision_form_widget.setLayout(precision_form)
        self._precision_sec.content_layout.addWidget(precision_form_widget)
        layout.addWidget(self._precision_sec)

        # ── Click-Lock ───────────────────────────────────────────────
        self._clicklock_sec = CollapsibleSection(
            tr("module.mouse.click_lock"),
            self._settings.get("click_lock_enabled", False),
            description=tr("module.mouse.click_lock.description"),
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
        clicklock_form.addRow("", self._clicklock_symbol_cb)

        clicklock_form_widget = QWidget()
        clicklock_form_widget.setLayout(clicklock_form)
        self._clicklock_sec.content_layout.addWidget(clicklock_form_widget)
        layout.addWidget(self._clicklock_sec)

        # ── Cursor highlight ─────────────────────────────────────────
        self._highlight_sec = CollapsibleSection(
            tr("module.mouse.highlight"),
            self._settings.get("highlight_enabled", False),
            description=tr("module.mouse.highlight.description"),
        )
        self._highlight_sec.toggled.connect(
            lambda v: self._save("highlight_enabled", v))

        highlight_form = QFormLayout()
        highlight_form.setSpacing(8)
        self._highlight_form = highlight_form

        self._highlight_hotkey = HotkeyEdit(
            self._settings.get("highlight_hotkey", ""), action_id="mouse.highlight")
        self._highlight_hotkey.key_changed.connect(
            lambda k: self._save("highlight_hotkey", k))
        highlight_form.addRow(tr("module.mouse.highlight.hotkey"),
                              self._highlight_hotkey)

        # Pulsing rings toggle
        self._highlight_rings_cb = QCheckBox(
            tr("module.mouse.highlight.rings"))
        self._highlight_rings_cb.setChecked(
            bool(self._settings.get("highlight_rings", True)))
        self._highlight_rings_cb.toggled.connect(self._on_rings_toggled)
        highlight_form.addRow("", self._highlight_rings_cb)

        # Ring style: open (like the WithEase logo) or a closed circle.
        self._highlight_ring_style = QComboBox()
        self._highlight_ring_style.addItem(
            tr("module.mouse.highlight.ring_style.open"), "open")
        self._highlight_ring_style.addItem(
            tr("module.mouse.highlight.ring_style.closed"), "closed")
        if self._settings.get("highlight_ring_style", "open") == "closed":
            self._highlight_ring_style.setCurrentIndex(1)
        self._highlight_ring_style.currentIndexChanged.connect(
            lambda i: self._save("highlight_ring_style",
                                 self._highlight_ring_style.itemData(i)))
        highlight_form.addRow(tr("module.mouse.highlight.ring_style"),
                              self._highlight_ring_style)

        # Colour picker (applies to rings)
        self._highlight_color = list(
            self._settings.get("highlight_color", [255, 140, 0]))
        from withease.gui.ui_utils import em
        self._highlight_color_btn = QPushButton()
        self._highlight_color_btn.setFixedWidth(max(80, em(5)))
        self._update_color_button()
        self._highlight_color_btn.clicked.connect(self._pick_highlight_color)
        highlight_form.addRow(tr("module.mouse.highlight.color"),
                              self._highlight_color_btn)

        # Pulse radius
        self._highlight_radius = QSlider(Qt.Orientation.Horizontal)
        self._highlight_radius.setRange(40, 200)
        self._highlight_radius.setValue(
            int(self._settings.get("highlight_radius", 90)))
        self._highlight_radius.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._highlight_radius.setTickInterval(20)
        self._highlight_radius.valueChanged.connect(
            lambda v: self._save("highlight_radius", v))
        highlight_form.addRow(tr("module.mouse.highlight.radius"),
                              self._highlight_radius)

        # Pulse duration
        from PySide6.QtWidgets import QDoubleSpinBox
        self._highlight_duration = QDoubleSpinBox()
        self._highlight_duration.setRange(0.5, 10.0)
        self._highlight_duration.setSingleStep(0.1)
        self._highlight_duration.setDecimals(1)
        self._highlight_duration.setSuffix(" s")
        self._highlight_duration.setValue(
            float(self._settings.get("highlight_duration", 1.6)))
        self._highlight_duration.valueChanged.connect(
            lambda v: self._save("highlight_duration", round(v, 1)))
        highlight_form.addRow(tr("module.mouse.highlight.duration"),
                              self._highlight_duration)

        # Direction arrow toggle
        self._highlight_arrow_cb = QCheckBox(
            tr("module.mouse.highlight.arrow"))
        self._highlight_arrow_cb.setChecked(
            bool(self._settings.get("highlight_arrow", False)))
        self._highlight_arrow_cb.toggled.connect(self._on_arrow_toggled)
        highlight_form.addRow("", self._highlight_arrow_cb)

        # Arrow thickness
        self._highlight_arrow_thickness = QSlider(Qt.Orientation.Horizontal)
        self._highlight_arrow_thickness.setRange(3, 30)
        self._highlight_arrow_thickness.setValue(
            int(self._settings.get("highlight_arrow_thickness", 6)))
        self._highlight_arrow_thickness.setTickPosition(
            QSlider.TickPosition.TicksBelow)
        self._highlight_arrow_thickness.setTickInterval(3)
        self._highlight_arrow_thickness.valueChanged.connect(
            lambda v: self._save("highlight_arrow_thickness", v))
        self._arrow_thickness_row_label = QLabel(
            tr("module.mouse.highlight.arrow_thickness"))
        highlight_form.addRow(self._arrow_thickness_row_label,
                              self._highlight_arrow_thickness)

        # Preview + reset buttons
        btn_row = QVBoxLayout()
        self._highlight_preview_btn = QPushButton(
            tr("module.mouse.highlight.preview"))
        self._highlight_preview_btn.clicked.connect(self._preview_highlight)
        btn_row.addWidget(self._highlight_preview_btn)

        self._highlight_reset_btn = QPushButton(
            tr("module.mouse.highlight.reset"))
        self._highlight_reset_btn.clicked.connect(self._reset_highlight)
        btn_row.addWidget(self._highlight_reset_btn)

        btn_row_widget = QWidget()
        btn_row_widget.setLayout(btn_row)
        highlight_form.addRow("", btn_row_widget)

        highlight_form_widget = QWidget()
        highlight_form_widget.setLayout(highlight_form)
        self._highlight_sec.content_layout.addWidget(highlight_form_widget)
        layout.addWidget(self._highlight_sec)
        # Initial row visibility – only AFTER the form is parented: calling
        # setRowVisible(True) on parentless rows briefly shows them as
        # stray top-level windows (visible as flicker on rebuilds).
        self._on_rings_toggled(self._highlight_rings_cb.isChecked())
        self._on_arrow_toggled(self._highlight_arrow_cb.isChecked())

        # ── Keyboard as mouse buttons ────────────────────────────────
        self._kbclick_sec = CollapsibleSection(
            tr("module.mouse.keyboard_clicks"),
            self._settings.get("keyboard_clicks_enabled", False),
            description=tr("module.mouse.keyboard_clicks.description"),
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
        grid_form.addRow(tr("module.mouse.screen_zones.grid"), self._grid_combo)
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

    def _pick_highlight_color(self) -> None:
        r, g, b = self._highlight_color
        chosen = QColorDialog.getColor(
            QColor(r, g, b), self, tr("module.mouse.highlight.color"))
        if chosen.isValid():
            self._highlight_color = [chosen.red(), chosen.green(), chosen.blue()]
            self._update_color_button()
            self._save("highlight_color", self._highlight_color)

    def _on_rings_toggled(self, enabled: bool) -> None:
        self._save("highlight_rings", enabled)
        # Style + colour + radius only apply to the rings
        self._highlight_form.setRowVisible(self._highlight_ring_style, enabled)
        self._highlight_form.setRowVisible(self._highlight_color_btn, enabled)
        self._highlight_form.setRowVisible(self._highlight_radius, enabled)
        # Keep at least one visible cue – otherwise the highlight shows nothing.
        if not enabled and not self._highlight_arrow_cb.isChecked():
            self._highlight_arrow_cb.setChecked(True)

    def _on_arrow_toggled(self, enabled: bool) -> None:
        self._save("highlight_arrow", enabled)
        # Thickness slider is only shown when the arrow is enabled
        self._highlight_form.setRowVisible(
            self._highlight_arrow_thickness, enabled)
        if not enabled and not self._highlight_rings_cb.isChecked():
            self._highlight_rings_cb.setChecked(True)

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

    def _reset_highlight(self) -> None:
        """Reset highlight appearance to defaults (activation key untouched)."""
        self._highlight_color = [255, 140, 0]
        self._update_color_button()
        self._save("highlight_color", self._highlight_color)

        self._highlight_rings_cb.setChecked(True)         # fires toggled → saves
        self._highlight_ring_style.setCurrentIndex(0)     # "open" (default)
        self._highlight_radius.setValue(90)               # fires valueChanged → saves
        self._highlight_duration.setValue(1.6)            # fires valueChanged → saves
        self._highlight_arrow_thickness.setValue(6)       # fires valueChanged → saves
        self._highlight_arrow_cb.setChecked(False)        # fires toggled → saves

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
        if self._zones_preview_cb.isChecked():
            self._zones_preview_cb.setChecked(False)
        super().hideEvent(event)  # type: ignore[arg-type]

    def _update_enabled_state(self, enabled: bool) -> None:
        for sec in self._sections:
            sec.setEnabled(enabled)
