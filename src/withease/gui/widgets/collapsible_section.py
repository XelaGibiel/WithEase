"""CollapsibleSection – a group box whose content collapses when unchecked.

The header is a single checkbox. When checked the content area is visible;
when unchecked only the header (and optional description) is shown.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from withease.gui import theme


class _HeaderStrip(QWidget):
    """The clickable header row of a CollapsibleSection.

    Deliberately only the HEADER, not the whole card: unlike a pure fold-out
    panel, this checkbox switches an assistance feature on and off.  Making
    the entire card (description text included) a toggle would turn a stray
    click into "Click-Lock is suddenly on" – and accidental clicks are exactly
    the difficulty many of these users have.  The header strip is a bounded,
    predictable target that still spans the full card width."""

    def mousePressEvent(self, event) -> None:  # noqa: N802
        section = self.parentWidget()
        toggle = getattr(section, "_checkbox", None)
        if toggle is not None:
            toggle.toggle()
            event.accept()
            return
        super().mousePressEvent(event)


class CollapsibleSection(QFrame):
    """A labelled checkbox that expands a content area when checked.

    Rendered as its own card (objectName "card" – see theme.app_stylesheet):
    the enable-checkbox is the card header, an optional description sits under
    it, and the collapsible settings appear below.  So each feature gets its
    own framed panel, consistent with the General page."""

    toggled = Signal(bool)  # emits the new checked state

    def __init__(self, label: str, checked: bool = False,
                 description: str = "", icon: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")             # card background + border + padding

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)   # padding comes from the card QSS
        outer.setSpacing(6)

        # Icon (optional) + checkbox in one row, left-aligned as a group so the
        # focus highlight hugs just the checkbox – matching the icon+title
        # pattern used by the card() helper elsewhere, so every feature is
        # identifiable at a glance the same way across the app.
        # The header is a widget (not a bare layout) so the WHOLE strip –
        # icon, label and the empty space beside it – toggles the feature,
        # instead of only the checkbox's own ~25px-tall label.
        header_w = _HeaderStrip(self)
        header = QHBoxLayout(header_w)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        if icon:
            icon_lbl = QLabel(icon)
            # Same fixed-size rule as card()'s icons (theme.py QLabel#cardIcon)
            # – stays put regardless of the font-size setting.
            icon_lbl.setObjectName("cardIcon")
            header.addWidget(icon_lbl)
        self._checkbox = QCheckBox(label)
        self._checkbox.setChecked(checked)
        self._checkbox.setStyleSheet("font-weight: bold;")
        self._checkbox.toggled.connect(self._on_toggle)
        header.addWidget(self._checkbox)
        header.addStretch()
        header_w.setCursor(Qt.CursorShape.PointingHandCursor)
        header_w.setMinimumHeight(theme.target_px())
        # The whole header toggles the checkbox (see _Header.mousePressEvent),
        # so THIS is the click target, not the 20px box inside it.  The flag
        # says so out loud, for ui_utils.compact_fields() (which would
        # otherwise enlarge the box needlessly) and for the click-target test
        # (which would otherwise report it as too small).
        header_w.setProperty("clickTarget", True)
        outer.addWidget(header_w)

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
        if checked:
            # Show what the click just revealed instead of leaving it below
            # the fold – see ui_utils.ensure_card_visible.
            from withease.gui.ui_utils import ensure_card_visible
            ensure_card_visible(self)
