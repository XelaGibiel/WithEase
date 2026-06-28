"""Mouse module settings page."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from accessmate.core.i18n import tr
from accessmate.gui.widgets.hotkey_edit import HotkeyEdit

if TYPE_CHECKING:
    from accessmate.modules.mouse import MouseModule


class MouseSettingsWidget(QWidget):
    def __init__(self, module: "MouseModule", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._settings = module._settings
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
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
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        # ── Centering ────────────────────────────────────────────────
        centering_box = QGroupBox(tr("module.mouse.centering"))
        centering_form = QFormLayout(centering_box)
        centering_form.setSpacing(10)

        self._centering_cb = QCheckBox()
        self._centering_cb.setChecked(self._settings.get("centering_enabled", False))
        self._centering_cb.toggled.connect(lambda v: self._save("centering_enabled", v))
        centering_form.addRow(tr("module.mouse.centering.enabled"), self._centering_cb)

        self._centering_delay = QSpinBox()
        self._centering_delay.setRange(1, 300)
        self._centering_delay.setSuffix(" s")
        self._centering_delay.setValue(int(self._settings.get("centering_delay", 5)))
        self._centering_delay.valueChanged.connect(
            lambda v: self._save("centering_delay", float(v)))
        centering_form.addRow(tr("module.mouse.centering.delay"), self._centering_delay)

        self._centering_countdown = QSpinBox()
        self._centering_countdown.setRange(0, 30)
        self._centering_countdown.setSuffix(" s")
        self._centering_countdown.setValue(
            int(self._settings.get("centering_countdown", 3)))
        self._centering_countdown.valueChanged.connect(
            lambda v: self._save("centering_countdown", v))
        centering_form.addRow(tr("module.mouse.centering.countdown"),
                              self._centering_countdown)

        self._centering_hotkey = HotkeyEdit(
            self._settings.get("centering_hotkey", ""))
        self._centering_hotkey.key_changed.connect(
            lambda k: self._save("centering_hotkey", k))
        centering_form.addRow(tr("module.mouse.centering.enabled") + " (Taste)",
                              self._centering_hotkey)

        layout.addWidget(centering_box)

        # ── Precision mode ───────────────────────────────────────────
        precision_box = QGroupBox(tr("module.mouse.precision"))
        precision_form = QFormLayout(precision_box)
        precision_form.setSpacing(10)

        self._precision_cb = QCheckBox()
        self._precision_cb.setChecked(
            self._settings.get("precision_mode_enabled", False))
        self._precision_cb.toggled.connect(
            lambda v: self._save("precision_mode_enabled", v))
        precision_form.addRow(tr("module.mouse.precision.enabled"), self._precision_cb)

        self._precision_slider = QSlider(Qt.Orientation.Horizontal)
        self._precision_slider.setRange(1, 10)
        self._precision_slider.setValue(int(self._settings.get("precision_speed", 3)))
        self._precision_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._precision_slider.setTickInterval(1)
        self._precision_slider.valueChanged.connect(
            lambda v: self._save("precision_speed", v))
        precision_form.addRow(tr("module.mouse.precision.speed"), self._precision_slider)

        self._precision_hotkey = HotkeyEdit(self._settings.get("precision_hotkey", ""))
        self._precision_hotkey.key_changed.connect(
            lambda k: self._save("precision_hotkey", k))
        precision_form.addRow(tr("module.mouse.precision.enabled") + " (Taste)",
                              self._precision_hotkey)

        layout.addWidget(precision_box)

        # ── Click-Lock ───────────────────────────────────────────────
        clicklock_box = QGroupBox(tr("module.mouse.click_lock"))
        clicklock_form = QFormLayout(clicklock_box)
        clicklock_form.setSpacing(10)

        self._clicklock_cb = QCheckBox()
        self._clicklock_cb.setChecked(self._settings.get("click_lock_enabled", False))
        self._clicklock_cb.toggled.connect(
            lambda v: self._save("click_lock_enabled", v))
        clicklock_form.addRow(tr("module.mouse.click_lock.enabled"), self._clicklock_cb)

        self._clicklock_hotkey = HotkeyEdit(
            self._settings.get("clicklock_hotkey", ""))
        self._clicklock_hotkey.key_changed.connect(
            lambda k: self._save("clicklock_hotkey", k))
        clicklock_form.addRow(tr("module.mouse.click_lock.enabled") + " (Taste)",
                              self._clicklock_hotkey)

        layout.addWidget(clicklock_box)

        # ── Keyboard as mouse buttons ────────────────────────────────
        kbclick_box = QGroupBox(tr("module.mouse.keyboard_clicks"))
        kbclick_form = QFormLayout(kbclick_box)
        kbclick_form.setSpacing(10)

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

        layout.addWidget(kbclick_box)

        # ── Screen zones ─────────────────────────────────────────────
        zones_box = QGroupBox(tr("module.mouse.screen_zones"))
        zones_layout = QVBoxLayout(zones_box)

        self._zones_cb = QCheckBox(tr("module.mouse.screen_zones.enabled"))
        self._zones_cb.setChecked(self._settings.get("screen_zones_enabled", False))
        self._zones_cb.toggled.connect(
            lambda v: self._save("screen_zones_enabled", v))
        zones_layout.addWidget(self._zones_cb)
        zones_layout.addWidget(QLabel("(Zone editor follows in v0.3)"))

        layout.addWidget(zones_box)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._groups = [centering_box, precision_box, clicklock_box,
                        kbclick_box, zones_box]
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _update_enabled_state(self, enabled: bool) -> None:
        for group in self._groups:
            group.setEnabled(enabled)
