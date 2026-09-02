"""Keyboard module settings page."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from withease.core.i18n import tr
from withease.gui.widgets.collapsible_section import CollapsibleSection
from withease.gui.widgets.key_list_edit import KeyListEdit
from withease.gui.ui_utils import label_with_hint, setting_note
from withease.gui import theme

if TYPE_CHECKING:
    from withease.modules.keyboard import KeyboardModule


class KeyboardSettingsWidget(QWidget):
    def __init__(self, module: "KeyboardModule", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._settings = module._settings
        self._build_ui()
        from withease.gui.settings.module_sync import sync_module_checkbox
        sync_module_checkbox(self, module, self._enabled_cb,
                             self._update_enabled_state)

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
            icon="⏳",
        )
        self._delay_sec.toggled.connect(lambda v: self._save("delay_enabled", v))

        delay_form = QFormLayout()
        delay_form.setSpacing(8)

        self._delay_ms = QSpinBox()
        self._delay_ms.setRange(50, 5000)
        self._delay_ms.setSuffix(" ms")
        self._delay_ms.setValue(int(self._settings.get("delay_ms", 500)))
        self._delay_ms.valueChanged.connect(lambda v: self._save("delay_ms", v))
        # Visible, not a tooltip: a value set too high makes the whole
        # keyboard look broken, and nobody hovers a caption to find that out.
        delay_form.addRow(tr("module.keyboard.delay.ms"), self._delay_ms)
        delay_form.addRow("", setting_note(tr("module.keyboard.delay.ms.hint")))

        self._delay_exceptions = KeyListEdit(
            self._settings.get("delay_exceptions", []))
        self._delay_exceptions.keys_changed.connect(
            lambda keys: self._save("delay_exceptions", keys))
        delay_form.addRow(
            label_with_hint(tr("module.keyboard.delay.exceptions"),
                            tr("module.keyboard.delay.exceptions.hint")),
            self._delay_exceptions)

        delay_form_widget = QWidget()
        delay_form_widget.setLayout(delay_form)
        self._delay_sec.content_layout.addWidget(delay_form_widget)
        layout.addWidget(self._delay_sec)

        # ── No-repeat protection (hold = single keystroke) ───────────
        self._norepeat_sec = CollapsibleSection(
            tr("module.keyboard.no_repeat"),
            self._settings.get("no_repeat_enabled", False),
            description=tr("module.keyboard.no_repeat.description"),
            icon="🔂",
        )
        self._norepeat_sec.toggled.connect(
            lambda v: self._save("no_repeat_enabled", v))

        norepeat_form = QFormLayout()
        norepeat_form.setSpacing(8)
        self._no_repeat_exceptions = KeyListEdit(
            self._settings.get("no_repeat_exceptions", []))
        self._no_repeat_exceptions.keys_changed.connect(
            lambda keys: self._save("no_repeat_exceptions", keys))
        norepeat_form.addRow(
            label_with_hint(tr("module.keyboard.no_repeat.exceptions"),
                            tr("module.keyboard.no_repeat.exceptions.hint")),
            self._no_repeat_exceptions)

        norepeat_form_widget = QWidget()
        norepeat_form_widget.setLayout(norepeat_form)
        self._norepeat_sec.content_layout.addWidget(norepeat_form_widget)
        layout.addWidget(self._norepeat_sec)

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
            icon="📌",
        )
        self._sticky_sec.toggled.connect(self._on_sticky_toggled)

        sticky_form = QFormLayout()
        sticky_form.setSpacing(8)

        # The caption lives ON the checkbox, not in the label column: five
        # rows of "text ......... [ ]" wasted a lot of height and left a wide
        # dead gap between the name and the box.  As one widget the whole
        # caption is clickable (a far bigger target than the 20px indicator)
        # and the block is much more compact.
        self._sticky_cbs: dict[str, QCheckBox] = {}
        for key in ("shift", "ctrl", "alt", "altgr", "win"):
            cb = QCheckBox(tr(f"module.keyboard.sticky.{key}"))
            cb.setChecked(self._settings.get(f"sticky_{key}", False))
            cb.toggled.connect(lambda v, k=key: self._save(f"sticky_{k}", v))
            sticky_form.addRow("", cb)
            self._sticky_cbs[key] = cb

        # Same treatment for the caption.
        self._sticky_auto = QCheckBox(
            tr("module.keyboard.sticky.auto_release"))
        self._sticky_auto.setChecked(
            self._settings.get("sticky_auto_release", True))
        self._sticky_auto.toggled.connect(
            lambda v: self._save("sticky_auto_release", v))
        # Visible: this switch does not turn a feature on or off, it decides
        # HOW Sticky Keys behaves.  Without the two examples the two states
        # cannot be told apart from the caption "Automatisch lösen".
        sticky_form.addRow("", self._sticky_auto)
        # attached: indented to the checkbox caption and hard up against it,
        # so it reads as an explanation of THAT switch and not of the card.
        sticky_form.addRow(
            "", setting_note(tr("module.keyboard.sticky.auto_release.hint"),
                             checkbox=True))

        from withease.gui.widgets.modifier_indicator import POSITIONS
        self._sticky_pos = QComboBox()
        for pos in POSITIONS:
            self._sticky_pos.addItem(tr(f"keyboard.indicator.pos.{pos}"), pos)
        saved_pos = self._settings.get("sticky_indicator_position", "bottom-right")
        idx = POSITIONS.index(saved_pos) if saved_pos in POSITIONS else 5
        self._sticky_pos.setCurrentIndex(idx)
        self._sticky_pos.currentIndexChanged.connect(self._on_position_changed)
        sticky_form.addRow(tr("keyboard.indicator.position"), self._sticky_pos)

        # Chip size (indicator + preview) moved to Allgemein – it's shared
        # with the macro-mode chip, not specific to the keyboard module.

        sticky_form_widget = QWidget()
        sticky_form_widget.setLayout(sticky_form)
        self._sticky_sec.content_layout.addWidget(sticky_form_widget)
        layout.addWidget(self._sticky_sec)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._sections = [self._delay_sec, self._norepeat_sec, self._sticky_sec]
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _on_sticky_toggled(self, enabled: bool) -> None:
        self._save("sticky_enabled", enabled)
        if enabled:
            # Enabling the tool without any modifier selected would do nothing –
            # preselect Shift so it actually works.
            if not any(cb.isChecked() for cb in self._sticky_cbs.values()):
                self._sticky_cbs["shift"].setChecked(True)  # fires toggled → saves
        else:
            # Release anything currently latched so no modifier stays held.
            self._module._release_all_sticky()

    def _on_position_changed(self, index: int) -> None:
        from withease.gui.widgets.modifier_indicator import POSITIONS
        pos = POSITIONS[index]
        self._save("sticky_indicator_position", pos)
        from withease.core.event_bus import bus
        bus.publish("keyboard.indicator_position", position=pos)

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _update_enabled_state(self, enabled: bool) -> None:
        for sec in self._sections:
            sec.setEnabled(enabled)
