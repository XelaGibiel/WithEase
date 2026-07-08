"""CollapsibleSection – a group box whose content collapses when unchecked.

The header is a single checkbox. When checked the content area is visible;
when unchecked only the header (and optional description) is shown.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from accessmate.gui import theme


class CollapsibleSection(QWidget):
    """A labelled checkbox that expands a content area when checked."""

    toggled = Signal(bool)  # emits the new checked state

    def __init__(self, label: str, checked: bool = False,
                 description: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._checkbox = QCheckBox(label)
        self._checkbox.setChecked(checked)
        self._checkbox.setStyleSheet("font-weight: bold;")
        self._checkbox.toggled.connect(self._on_toggle)
        outer.addWidget(self._checkbox)

        if description:
            self._desc_label = QLabel(description)
            self._desc_label.setStyleSheet(
                theme.hint_style("padding-left: 20px;"))
            self._desc_label.setWordWrap(True)
            outer.addWidget(self._desc_label)
        else:
            self._desc_label = None

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 4, 0, 4)
        self._content_layout.setSpacing(8)
        outer.addWidget(self._content)

        self._content.setVisible(checked)

    # ------------------------------------------------------------------
    # Public API

    @property
    def content_layout(self) -> QVBoxLayout:
        """Add child widgets here."""
        return self._content_layout

    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def set_checked(self, value: bool) -> None:
        self._checkbox.setChecked(value)

    # ------------------------------------------------------------------

    def _on_toggle(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self.toggled.emit(checked)
