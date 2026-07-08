"""Keyboard module settings page."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from accessmate.core.i18n import tr
from accessmate.gui.widgets.collapsible_section import CollapsibleSection
from accessmate.gui.widgets.key_list_edit import KeyListEdit
from accessmate.gui import theme

if TYPE_CHECKING:
    from accessmate.modules.keyboard import KeyboardModule


class KeyboardSettingsWidget(QWidget):
    def __init__(self, module: "KeyboardModule", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._settings = module._settings
        self._build_ui()
        from accessmate.gui.settings.module_sync import sync_module_checkbox
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
        self._enabled_cb = QCheckBox(tr("module.keyboard.enabled"))
        self._enabled_cb.setChecked(self._module.enabled)
        self._enabled_cb.setStyleSheet(theme.title_style())
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── Key delay ────────────────────────────────────────────────
        self._delay_sec = CollapsibleSection(
            tr("module.keyboard.delay"),
            self._settings.get("delay_enabled", False),
            description=tr("module.keyboard.delay.description"),
        )
        self._delay_sec.toggled.connect(lambda v: self._save("delay_enabled", v))

        delay_form = QFormLayout()
        delay_form.setSpacing(8)

        self._delay_ms = QSpinBox()
        self._delay_ms.setRange(50, 5000)
        self._delay_ms.setSuffix(" ms")
        self._delay_ms.setValue(int(self._settings.get("delay_ms", 500)))
        self._delay_ms.valueChanged.connect(lambda v: self._save("delay_ms", v))
        delay_form.addRow(tr("module.keyboard.delay.ms"), self._delay_ms)

        self._delay_exceptions = KeyListEdit(
            self._settings.get("delay_exceptions", []))
        self._delay_exceptions.keys_changed.connect(
            lambda keys: self._save("delay_exceptions", keys))
        delay_form.addRow(tr("module.keyboard.delay.exceptions"),
                          self._delay_exceptions)

        delay_form_widget = QWidget()
        delay_form_widget.setLayout(delay_form)
        self._delay_sec.content_layout.addWidget(delay_form_widget)
        layout.addWidget(self._delay_sec)

        # ── Sticky Keys ──────────────────────────────────────────────
        sticky_enabled = self._settings.get(
            "sticky_enabled",
            any(self._settings.get(f"sticky_{k}", False)
                for k in ("shift", "ctrl", "alt", "altgr", "win")),
        )
        self._sticky_sec = CollapsibleSection(
            tr("module.keyboard.sticky"),
            sticky_enabled,
            description=tr("module.keyboard.sticky.description"),
        )
        self._sticky_sec.toggled.connect(self._on_sticky_toggled)

        sticky_form = QFormLayout()
        sticky_form.setSpacing(8)

        self._sticky_cbs: dict[str, QCheckBox] = {}
        for key in ("shift", "ctrl", "alt", "altgr", "win"):
            cb = QCheckBox()
            cb.setChecked(self._settings.get(f"sticky_{key}", False))
            cb.toggled.connect(lambda v, k=key: self._save(f"sticky_{k}", v))
            sticky_form.addRow(tr(f"module.keyboard.sticky.{key}"), cb)
            self._sticky_cbs[key] = cb

        self._sticky_auto = QCheckBox()
        self._sticky_auto.setChecked(
            self._settings.get("sticky_auto_release", True))
        self._sticky_auto.toggled.connect(
            lambda v: self._save("sticky_auto_release", v))
        sticky_form.addRow(tr("module.keyboard.sticky.auto_release"),
                           self._sticky_auto)

        from accessmate.gui.widgets.modifier_indicator import POSITIONS
        self._sticky_pos = QComboBox()
        for pos in POSITIONS:
            self._sticky_pos.addItem(tr(f"keyboard.indicator.pos.{pos}"), pos)
        saved_pos = self._settings.get("sticky_indicator_position", "bottom-right")
        idx = POSITIONS.index(saved_pos) if saved_pos in POSITIONS else 5
        self._sticky_pos.setCurrentIndex(idx)
        self._sticky_pos.currentIndexChanged.connect(self._on_position_changed)
        sticky_form.addRow(tr("keyboard.indicator.position"), self._sticky_pos)

        self._sticky_chip_size = QSpinBox()
        self._sticky_chip_size.setRange(16, 64)
        self._sticky_chip_size.setSuffix(" px")
        self._sticky_chip_size.setValue(int(self._settings.get("sticky_chip_size", 24)))
        self._sticky_chip_size.valueChanged.connect(self._on_chip_size_changed)

        self._sticky_preview_cb = QCheckBox(tr("keyboard.indicator.preview"))
        self._sticky_preview_cb.toggled.connect(self._on_sticky_preview_toggled)

        chip_row = QWidget()
        chip_row_layout = QHBoxLayout(chip_row)
        chip_row_layout.setContentsMargins(0, 0, 0, 0)
        chip_row_layout.setSpacing(8)
        chip_row_layout.addWidget(self._sticky_chip_size)
        chip_row_layout.addWidget(self._sticky_preview_cb)
        chip_row_layout.addStretch()
        sticky_form.addRow(tr("keyboard.indicator.chip_size"), chip_row)

        sticky_form_widget = QWidget()
        sticky_form_widget.setLayout(sticky_form)
        self._sticky_sec.content_layout.addWidget(sticky_form_widget)
        layout.addWidget(self._sticky_sec)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._sections = [self._delay_sec, self._sticky_sec]
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _on_sticky_toggled(self, enabled: bool) -> None:
        self._save("sticky_enabled", enabled)
        if not enabled:
            # Release anything currently latched so no modifier stays held.
            self._module._release_all_sticky()

    def _on_position_changed(self, index: int) -> None:
        from accessmate.gui.widgets.modifier_indicator import POSITIONS
        pos = POSITIONS[index]
        self._save("sticky_indicator_position", pos)
        from accessmate.core.event_bus import bus
        bus.publish("keyboard.indicator_position", position=pos)

    def _on_chip_size_changed(self, size: int) -> None:
        self._save("sticky_chip_size", size)
        from accessmate.core.event_bus import bus
        bus.publish("keyboard.chip_size", size=size)

    def _on_sticky_preview_toggled(self, active: bool) -> None:
        from accessmate.core.event_bus import bus
        bus.publish("keyboard.preview", active=active)

    def hideEvent(self, event: object) -> None:  # type: ignore[override]
        if self._sticky_preview_cb.isChecked():
            self._sticky_preview_cb.setChecked(False)
        super().hideEvent(event)  # type: ignore[arg-type]

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _update_enabled_state(self, enabled: bool) -> None:
        for sec in self._sections:
            sec.setEnabled(enabled)
