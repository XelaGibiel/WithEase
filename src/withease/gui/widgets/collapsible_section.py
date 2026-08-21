"""CollapsibleSection – a group box whose content collapses when unchecked.

The header is a single checkbox. When checked the content area is visible;
when unchecked only the header (and optional description) is shown.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout, QWidget

from withease.gui import theme


class CollapsibleSection(QFrame):
    """A labelled checkbox that expands a content area when checked.

    Rendered as its own card (objectName "card" – see theme.app_stylesheet):
    the enable-checkbox is the card header, an optional description sits under
    it, and the collapsible settings appear below.  So each feature gets its
    own framed panel, consistent with the General page."""

    toggled = Signal(bool)  # emits the new checked state

    def __init__(self, label: str, checked: bool = False,
                 description: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")             # card background + border + padding

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)   # padding comes from the card QSS
        outer.setSpacing(6)

        self._checkbox = QCheckBox(label)
        self._checkbox.setChecked(checked)
        self._checkbox.setStyleSheet("font-weight: bold;")
        self._checkbox.toggled.connect(self._on_toggle)
        # Left-aligned so it sizes to its label – the focus highlight then hugs
        # the checkbox instead of spanning the whole card width.
        outer.addWidget(self._checkbox, 0, Qt.AlignmentFlag.AlignLeft)

        if description:
            self._desc_label = QLabel(description)
            # Symmetric left/right inset so the wrapped description keeps an even
            # margin on both sides of the card (at large font sizes it otherwise
            # ran to the right card edge while the left stayed indented).
            self._desc_label.setStyleSheet(
                theme.hint_style("padding-left: 22px; padding-right: 22px;"))
            self._desc_label.setWordWrap(True)
            outer.addWidget(self._desc_label)
        else:
            self._desc_label = None

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(22, 6, 22, 2)   # symmetric
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
